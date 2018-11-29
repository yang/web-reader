# -*- coding: utf-8 -*-

"""
SoundGecko clone - a web app to convert web pages to an MP3 podcast feed.
"""
import base64
import json

from argparse import ArgumentParser, ArgumentTypeError

import subprocess as subp
from datetime import datetime
from email.mime.text import MIMEText
import logging
import re
from smtplib import SMTP
import sys
import tempfile
import traceback
import socket
from feedgen.feed import FeedGenerator
import nltk
from pq import PQ
import requests
import flask
from flask import request
from boilerpipe.extract import Extractor
import pydub
import path
from goose import Goose
import ftfy
from slugify.slugify import slugify
import sqlalchemy as sa
from sqlalchemy.engine import create_engine
from sqlalchemy.ext.declarative.api import declarative_base
from sqlalchemy.orm.scoping import scoped_session
from sqlalchemy.orm.session import sessionmaker
from flask.ext.cors import cross_origin
import time
import io

__author__ = 'yang'

def valid_date(s):
  """
  From <https://stackoverflow.com/questions/25470844/specify-format-for-input-arguments-argparse-python>
  :param s: The input string to parse as YYYY-MM-DD.
  :type s: str
  :return: The parsed date.
  :rtype: datetime.date
  """
  try:
    return datetime.strptime(s, "%Y-%m-%d")
  except ValueError:
    raise ArgumentTypeError("Not a valid date: %r." % (s,))

UA = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36'

app = flask.Flask(__name__)

log = logging.getLogger(__name__)

sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
goose = Goose()

alpha = re.compile(r'[a-z]', re.IGNORECASE)
newlines = re.compile(r'\n+')

db_session = None
queue = None

mp3dir = path.path('~/.webreader/mp3s').expanduser()

Base = declarative_base()
class Article(Base):
  __tablename__ = 'articles'
  id = sa.Column(sa.Integer, primary_key=True)
  url = sa.Column(sa.String)
  created = sa.Column(sa.DateTime, nullable=False)
  title = sa.Column(sa.String)
  body = sa.Column(sa.String)
  converted = sa.Column(sa.DateTime)

def swallow(f):
  # noinspection PyBroadException
  try: return f()
  except: return None

def extract(html):
  extractor = Extractor(extractor='ArticleExtractor', html=html)
  bp_text = extractor.getText()

  try:
    extracted = goose.extract(raw_html=html)
  except IndexError:
    # Tolerate https://github.com/grangier/python-goose/issues/194
    return '', bp_text
  else:
    gs_text = extracted.cleaned_text
    title = extracted.title
    return title, bp_text if len(bp_text) > len(gs_text) else gs_text

@app.route('/api/v1/enqueue', methods=['GET','POST'])
@cross_origin()
def enqueue():
  data = request.args if request.method == 'GET' else request.get_json()
  if app.config.get('secret') is not None and app.config.get('secret') != data.get('secret'):
    raise Exception('secret does not match!')
  with db_session.begin():
    import pprint; pprint.pprint(data)
    url = data.get('url')
    body = data.get('body') or None
    if (url or '').strip() == '' and (body or '').strip() == '':
      raise Exception('must provide at least url or body')
    article = Article(url=url, body=body, created=datetime.now())
    db_session.add(article)
    db_session.flush()
  queue.put(dict(article_id=article.id))
  return flask.jsonify(done=True)

@app.route('/feed')
def feed():
  if app.config.get('secret') is not None and app.config.get('secret') != request.args.get('key'):
    raise Exception('secret does not match!')
  limit = min(int(request.args.get('limit', 30)), 30)
  fg = FeedGenerator()
  fg.load_extension('podcast')
  fg.id('http://yz.mit.edu/audiolizard/feed')
  fg.title('AudioLizard podcast feed')
  fg.description('blah')
  fg.link(href='http://yz.mit.edu/audiolizard', rel='alternate')
  fg.link(href='http://yz.mit.edu/audiolizard/feed', rel='self')
  fg.language('en')
  with db_session.begin():
    articles = db_session.query(Article)\
      .filter(Article.converted != None)\
      .order_by(Article.created.desc())\
      .limit(limit)
    for article in articles:
      fe = fg.add_entry()
      fe.id('http://yz.mit.edu/audiolizard/mp3/%s' % article.id)
      fe.title(ftfy.fix_text(article.title or article.body[:100]))
      fe.description(ftfy.fix_text(article.body))
      fe.link(href=article.url)
      fe.enclosure('http://yz.mit.edu/audiolizard/mp3/%s' % article.id, 0, 'audio/mpeg')
    return flask.Response(fg.rss_str(pretty=True), mimetype='application/rss+xml')

@app.route('/mp3/<int:article_id>')
def mp3(article_id):
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    slug = slugify(article.title or article.body, max_length=256,
                   word_boundary=True, save_order=True)
    filename = '%s - %s.mp3' % (article_id, slug)
    best_path = enhanced_mp3_path(article)
    if not best_path.exists():
      best_path = mp3path(article)
    with open(best_path) as f:
      return flask.Response(f.read(), mimetype='audio/mpeg',
          headers={"Content-Disposition": "attachment; filename=%s" % filename})

@app.route('/mp3/<int:article_id>/enhance')
def enhance_get(article_id):
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    if enhanced_mp3_path(article).exists():
      return 'already enhanced'
    else:
      return '<form method="POST"><button type="submit">enhance!</button></form>'

@app.route('/mp3/<int:article_id>/enhance', methods=['POST'])
def enhance_post(article_id):
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    queue.put(dict(article_id=article.id, enhanced=True))
  return 'Done!'

def convert(url, outpath, enhanced=False):
  resp = get_with_retries(url, verify=False, headers={'user-agent': UA})

  raw_title, raw_text = extract(resp.content)
  text = ftfy.fix_text(raw_text)
  title = ftfy.fix_text(raw_title) if len(raw_title.strip()) > 0 else ''

  return convert_text(title, text, outpath, enhanced)

splitters = [
    re.compile(r'[:;]| -+ |\.{2,}|--+|—'),
    re.compile(r'[,]'),
]

# API supports max 5000 chars per request.
def segments(sents, maxchars=5000):
  i = 0
  while True:
    curseg = []
    count = 0
    while i < len(sents):
      sent = sents[i]
      if len(curseg) > 0 and count + 5 + len(sent) >= maxchars:
        break
      curseg.append(sent)
      count += 5 + len(sent)
      i += 1
    if len(curseg) > 0:
      # Sentences better be split with ". " or ".\n" - if you split with two spaces ".  "
      # then the API doesn't pause for very long in between sentences, for some reason!
      yield '.\n'.join(curseg)
    else:
      break

def convert_text(title, text, outpath, enhanced=False):
  log.info('converting %s', title)

  sents = [
    sent
    for par in [title] + newlines.split(text.strip())
    if par and alpha.search(par)
    for sent in sent_detector.tokenize(par.strip())
    if alpha.search(sent)
  ]

  segs = list(segments(sents))

  auth_key = subp.check_output('gcloud auth application-default print-access-token'.split()).strip()

  tempdir = path.path(tempfile.mkdtemp('web-reader'))
  ":type: path.Path"

  log.info('spooling %s sentences to temp dir %s', len(segs), tempdir)
  for i, seg in enumerate(segs):
    headers = {
      "Authorization": "Bearer " + auth_key,
      "Content-Type": "application/json; charset=utf-8",
    }
    data = {
      'input':{
        'text': seg
      },
      'voice':{
        'languageCode': 'en-US',
        'name':'en-US-Wavenet-F' if enhanced else 'en-US-Standard-C'
      },
      'audioConfig':{
        'audioEncoding':'MP3'
      }
    }
    resp = post_with_retries('https://texttospeech.googleapis.com/v1/text:synthesize',
                            data=json.dumps(data), headers=headers, debug_desc=seg)
    resp.raise_for_status()
    data = base64.b64decode(resp.json()['audioContent'])
    (tempdir / ('%s.mp3' % i)).write_bytes(data)

  combined = reduce(
    lambda x,y: x + y,
    (pydub.AudioSegment.from_mp3(tempdir / ('%s.mp3' % i)) for i in xrange(len(segs)))
  )
  combined = combined + pydub.AudioSegment.silent(1000)
  ":type: pydub.AudioSegment"
  # -b:a gives us CBR encoding (see https://trac.ffmpeg.org/wiki/Encode/MP3).
  # We need this because Android's built-in media seeker doesn't correctly seek in VBR
  # (see https://code.google.com/p/android/issues/detail?id=8154).
  combined.export(outpath, format='mp3', parameters=['-b:a','48000'])

  log.info('done converting %s', title)
  return title, text

def req_with_retries(method, url, debug_desc, **kw):
  """
  :param debug_desc: What to print instead of the URL when logging retries.
  :rtype: requests.Response
  """
  a, b, c = None, None, None # just to silence pycharm code check
  details = '%s with %s' % (url, kw)
  debug_desc = '%s (%s)' % (debug_desc, details) if debug_desc else details
  for trial in xrange(5):
    try:
      return getattr(requests, method)(url, timeout=30, **kw)
    except:
      log.warn('used trial #%s of 5 on %s', trial + 1, debug_desc)
      a, b, c = sys.exc_info()
      if trial + 1 < 5: time.sleep(5)
  else:
    raise a, b, c

def get_with_retries(url, debug_desc=None, **kw):
  return req_with_retries('get', url, debug_desc, **kw)
def post_with_retries(url, debug_desc=None, **kw):
  return req_with_retries('post', url, debug_desc, **kw)

def init_db(pq, engine):
  Base.metadata.drop_all(bind=engine)
  Base.metadata.create_all(bind=engine)
  pq.create()

def mp3path(article):
  return mp3dir / ('%s.mp3' % article.id)

def enhanced_mp3_path(article):
  return mp3dir / ('%s-enhanced.mp3' % article.id)

def create_session():
  engine = create_engine('postgresql://webreader@localhost/webreader')
  pq = PQ(engine.raw_connection())
  db_session = scoped_session(sessionmaker(autocommit=True,
                                           autoflush=False,
                                           bind=engine))
  return pq, db_session

def trunc_txt(s, max_chars=100):
  return s if len(s) < max_chars else s[:max_chars - 3] + '...'

def resubmit(base_url, sort_order, pretend, limit=None, min_date=None):
  pg, ses = create_session()
  # Choose the longest body
  failures = ses.connection().execute(
    sa.text('''
      select
        max(created) as created,
        url,
        (array_agg(body order by length(body) desc))[1] as body
      from articles
      where trim(coalesce(url, '')) != ''
      group by url
      having bool_and(converted is null) and (:min_created is null or max(created) >= :min_created)
      order by max(created) %(sort_order)s
      limit :limit
    ''' % dict(sort_order=sort_order)),
    min_created=min_date,
    limit=limit,
  ).fetchall()
  for created, url, body in failures:
    log.info('submitting URL %s body %r', url, trunc_txt(body or ''))
    if not pretend:
      requests.get(path.path(base_url) / 'api/v1/enqueue', params=dict(url=url, body=body))

def reconvert(min_id, max_id, sort_order, pretend):
  pg, ses = create_session()
  # Choose the longest body
  ids = ses.connection().execute(
    sa.text('''
      select id
      from articles
      where id between :min_id and :max_id
      order by id %(sort_order)s
    ''' % dict(sort_order=sort_order)),
    min_id=min_id,
    max_id=max_id
  ).fetchall()
  for [id] in ids:
    log.info('re-converting ID %s', id)
    if not pretend:
      queue.put(dict(article_id=id))

def main(argv=sys.argv):
  global engine, db_session, pq, queue

  logging.basicConfig()
  log.setLevel(logging.INFO)

  p = ArgumentParser(description=__doc__)
  subparsers = p.add_subparsers(help='sub-command help', dest='cmd')
  init_p = subparsers.add_parser('init')
  converter_p = subparsers.add_parser('converter')
  webserver_p = subparsers.add_parser('webserver')
  convert_p = subparsers.add_parser('convert')
  convert_file_p = subparsers.add_parser('convert-file')
  resubmit_p = subparsers.add_parser('resubmit')
  reconvert_p = subparsers.add_parser('reconvert')

  webserver_p.add_argument('-p', '--port', type=int,
                           help='Web server listen port')
  webserver_p.add_argument('-s', '--secret',
                           help='Optional parameter `secret` to restrict enqueuing')

  converter_p.add_argument('-t', '--to',
                           help='Email to send notifications to (sent only if this is set)')
  converter_p.add_argument('-f', '--from', default='audiolizard@' + socket.getfqdn(),
                           help='Email to send notifications as')
  converter_p.add_argument('--base-url',
                          help='The base URL to use in email links http://localhost:5000/ (excludes /api/...)')

  convert_p.add_argument('url', help='URL to fetch')
  convert_p.add_argument('outpath', help='Output MP3 path')

  convert_file_p.add_argument('path', help='Text file to read')
  convert_file_p.add_argument('outpath', help='Output MP3 path')

  resubmit_p.add_argument('-n', '--limit', type=int, default=10,
                          help='Limit to resubmitting this many failures')
  resubmit_p.add_argument('-d', '--min-date', type=valid_date,
                          help='Only resubmit failures on/after this date')
  resubmit_p.add_argument('-o', '--order', choices=['oldest','newest'], default='newest',
                          help='Whether to resubmit oldest or newest first')
  resubmit_p.add_argument('--pretend', action='store_true',
                          help='Only print the would-be resubmissions')
  resubmit_p.add_argument('base_url',
                          help='Where to resubmit, e.g. http://localhost:5000/ (excludes /api/...)')

  reconvert_p.add_argument('min_id', type=int,
                          help='Minimum ID to include')
  reconvert_p.add_argument('max_id', type=int,
                          help='Maximum ID to include')
  reconvert_p.add_argument('-o', '--order', choices=['oldest','newest'], default='newest',
                          help='Whether to reconvert oldest or newest first')
  reconvert_p.add_argument('--pretend', action='store_true',
                          help='Only print the would-be resubmissions')

  cfg = p.parse_args(argv[1:])
  cmd = cfg.cmd

  log.info('command-line config: %r', cfg)

  pq, db_session = create_session()

  if cmd == 'init':
    mp3dir.makedirs_p()
    init_db(pq, db_session.bind)
    return

  queue = pq['articles']

  if cmd == 'converter':
    while True:
      with db_session.begin():
        task = queue.get()
        if task is not None:
          article = db_session.query(Article).get(task.data['article_id'])
          log.info('processing %s', article.url)
          enhanced = bool(task.data.get('enhanced'))
          outpath = enhanced_mp3_path(article) if enhanced else mp3path(article)
          try:
            if article.body is not None:
              convert_text(None, article.body, outpath, enhanced)
            else:
              article.title, article.body = convert(article.url, outpath, enhanced)
          except Exception as ex:
            log.exception('error processing article')
            subj = 'AudioLizard | Error processing article'
            msg = '\n\n'.join([article.url, traceback.format_exc(), article.body or ''])
          else:
            article.converted = datetime.now()
            subj = 'AudioLizard | %s' % article.title or article.url
            mp3_url = path.path(cfg.base_url) / 'mp3' / str(article.id) if cfg.base_url else ''
            enhance_url = path.path(cfg.base_url) / 'mp3' / str(article.id) / 'enhance' if cfg.base_url else ''
            msg = '\n\n'.join(filter(None, [article.title or '', article.url, mp3_url, enhance_url, article.body or '']))
          if cfg.to:
            msg = MIMEText(msg, 'plain', 'utf-8')
            msg['Subject'] = subj
            msg['To'] = cfg.to
            msg['From'] = getattr(cfg, 'from')
            print subj
            s = SMTP('localhost')
            s.sendmail(getattr(cfg, 'from'), [cfg.to], msg.as_string())
            s.quit()
  elif cmd == 'webserver':
    app.config['CORS_HEADERS'] = 'Content-Type'
    if cfg.secret: app.config['secret'] = cfg.secret
    app.run(port=cfg.port)
  elif cmd == 'convert':
    convert(cfg.url, cfg.outpath)
  elif cmd == 'convert-file':
    convert_text(None, io.open(cfg.path, encoding='utf8').read(), cfg.outpath)
  elif cmd == 'resubmit':
    resubmit(
      cfg.base_url,
      pretend=cfg.pretend,
      sort_order=dict(newest='desc', oldest='asc')[cfg.order],
      limit=cfg.limit,
      min_date=cfg.min_date,
    )
  elif cmd == 'reconvert':
    reconvert(
      min_id=cfg.min_id,
      max_id=cfg.max_id,
      pretend=cfg.pretend,
      sort_order=dict(newest='desc', oldest='asc')[cfg.order],
    )
  else:
    raise Exception()
