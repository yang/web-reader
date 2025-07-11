# -*- coding: utf-8 -*-

"""
SoundGecko clone - a web app to convert web pages to an MP3 podcast feed.
"""
import base64
import json
import os

from argparse import ArgumentParser, ArgumentTypeError

import itertools
from multiprocessing import Process, Queue
import subprocess as subp
from datetime import datetime
import pytz
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
import pathlib
import ftfy
from slugify.slugify import slugify
import sqlalchemy as sa
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.scoping import scoped_session
from sqlalchemy.orm.session import sessionmaker
from flask_cors import cross_origin
import time
import io

__author__ = 'yang'

class TunneledException(Exception):
  pass

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

alpha = re.compile(r'[a-z]', re.IGNORECASE)
newlines = re.compile(r'\n+')

db_session = None
queue = None

mp3dir = pathlib.Path('~/.webreader/mp3s').expanduser()

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
  import trafilatura
  extracted = trafilatura.extract(html, include_comments=False)
  doc = trafilatura.extract_metadata(html)
  # doc can be None if we're looking at non-HTML plain text file
  return doc.title if doc else None, extracted

@app.route('/api/v1/enqueue', methods=['GET','POST'])
@cross_origin()
def enqueue():
  data = request.args if request.method == 'GET' else request.get_json()
  check_secret(data.get('key'))
  with db_session.begin():
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
  check_secret()
  limit = min(int(request.args.get('limit', 99)), 99)
  fg = FeedGenerator()
  fg.load_extension('podcast')
  base_url = app.config['base_url']
  fg.id('%s/feed' % base_url)
  fg.title('AudioLizard podcast feed')
  fg.description('blah')
  fg.link(href=('%s' % base_url), rel='alternate')
  fg.link(href=('%s/feed?key=%s' % (base_url, app.config.get('secret'))), rel='self')
  fg.language('en')
  with db_session.begin():
    articles = db_session.query(Article)\
      .filter(Article.converted != None)\
      .order_by(Article.created.desc())\
      .limit(limit)
    for article in articles:
      fe = fg.add_entry()
      mp3_url = '%s/mp3/%s?key=%s' % (base_url, article.id, app.config.get('secret'))
      fe.id(mp3_url)
      fe.title(ftfy.fix_text(article.title or (article.body or '(empty)')[:100]))
      fe.description(ftfy.fix_text(article.body or '(empty)'))
      fe.link(href=article.url)
      fe.pubDate(article.created.replace(tzinfo=pytz.UTC))
      fe.enclosure(mp3_url, 0, 'audio/mpeg')
    return flask.Response(fg.rss_str(pretty=True), mimetype='application/rss+xml')

class UnauthException(Exception):
  status_code = 401

  def __init__(self, message, status_code=None, payload=None):
    Exception.__init__(self)
    self.message = message
    if status_code is not None:
      self.status_code = status_code
    self.payload = payload

  def to_dict(self):
    rv = dict(self.payload or ())
    rv['message'] = self.message
    return rv

@app.errorhandler(UnauthException)
def handle_invalid_usage(error):
  response = flask.jsonify(error.to_dict())
  response.status_code = error.status_code
  return response

def check_secret(key = None):
  return
  if app.config.get('secret') is not None and app.config.get('secret') != (key or request.args.get('key') or
                                                                           request.args.get('secret')):
    raise UnauthException('secret does not match!')

@app.route('/mp3/<int:article_id>')
def mp3(article_id):
  check_secret()
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    slug = slugify(article.title or article.body, max_length=256,
                   word_boundary=True, save_order=True)
    filename = '%s - %s.mp3' % (article_id, slug)
    best_path = enhanced_mp3_path(article)
    if not best_path.exists():
      best_path = mp3path(article)
    with open(best_path, 'rb') as f:
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
  check_secret()
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    queue.put(dict(article_id=article.id, enhanced=True))
  return 'Done!'

def convert(url, outpath, enhanced=False):
  resp = get_with_retries(url, verify=False, headers={'user-agent': UA})

  log.info('gotten, extracting')
  raw_title, raw_text = extract(resp.text)
  log.info('ftfy')
  text = ftfy.fix_text(raw_text)
  title = ftfy.fix_text(raw_title) if len(raw_title.strip()) > 0 else ''

  return convert_text(title, text, outpath, enhanced)

splitters = [
    re.compile(r'[:;]| -+ |\.{2,}|--+|—'),
    re.compile(r'[,]'),
]

# API supports max 5000 bytes per request.
def segments(sents, maxbytes=5000):
  i = 0
  while True:
    curseg = []
    count = 0
    while i < len(sents):
      sent = sents[i]
      padding = 5
      if len(curseg) > 0 and count + padding + len(sent.encode('utf8')) >= maxbytes:
        break
      curseg.append(sent)
      count += padding + len(sent.encode('utf8'))
      i += 1
    if len(curseg) > 0:
      # Sentences better be split with ". " or ".\n" - if you split with two spaces ".  "
      # then the API doesn't pause for very long in between sentences, for some reason!
      yield '.\n'.join(curseg)
    else:
      break

def convert_text(title, text, outpath, enhanced=False):
  log.info('converting %s', title)
  sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')

  log.info('sentence segmentation')
  paragraphs = newlines.split(text.strip())
  sents = [
    sent
    for par in [title] + paragraphs
    if par and alpha.search(par)
    for sent in sent_detector.tokenize(par.strip())
    if alpha.search(sent)
  ]

  log.info('grouping into chunks')
  segs = list(segments(sents))

  auth_key = subp.check_output('gcloud auth application-default print-access-token'.split()).strip().decode('utf8')

  tempdir = pathlib.Path(tempfile.mkdtemp('web-reader'))

  log.info('spooling %s segments (%s paragraphs, %s sentences) to temp dir %s', len(segs), len(paragraphs), len(sents), tempdir)
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
        'name':'en-US-Wavenet-F' if enhanced else 'en-US-Standard-D'
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

  # From https://stackoverflow.com/questions/5276253/create-a-silent-mp3-from-the-command-line
  # Must use 24kHz to match the mp3s from Google (without needing transcoding)
  subp.check_call('''
  ffmpeg -hide_banner -loglevel error -f lavfi -i anullsrc=r=24000:cl=mono -t 0.5 -q:a 9 -acodec libmp3lame %s
  ''' % (tempdir / 'join-silence.mp3'), shell=True)
  subp.check_call('''
  ffmpeg -hide_banner -loglevel error -f lavfi -i anullsrc=r=24000:cl=mono -t 1 -q:a 9 -acodec libmp3lame %s
  ''' % (tempdir / 'end-silence.mp3'), shell=True)

  # From https://superuser.com/questions/314239/how-to-join-merge-many-mp3-files
  # join_spec should look like:
  # /tmp/tmplVPQHgweb-reader/0.mp3|/tmp/tmplVPQHgweb-reader/join-silence.mp3|/tmp/tmplVPQHgweb-reader/1.mp3|/tmp/tmplVPQHgweb-reader/end-silence.mp3
  join_spec = '|'.join(list(itertools.chain(
    *zip(
      [str(tempdir / ('%s.mp3' % i)) for i in range(len(segs))],
      [str( tempdir / 'join-silence.mp3' ) for i in range(len(segs))]
    )
  ))[:-1] + [str(tempdir / 'end-silence.mp3')])

  subp.check_call('''ffmpeg -hide_banner -loglevel error -i 'concat:%s' -acodec copy %s''' % (join_spec, outpath), shell=True)

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
  _ex = None
  for trial in range(5):
    try:
      resp = getattr(requests, method)(url, timeout=30, **kw)
      try:
        resp.raise_for_status()
        return resp
      except Exception as ex:
        log.warn(f'got API status {resp.status_code} error {resp.content}')
        raise
    except Exception as ex:
      log.warn(f'used trial #{trial + 1} of 5 on {debug_desc} for data {kw["data"] if "data" in kw else None}')
      _ex = ex
      if trial + 1 < 5: time.sleep(5)
  else:
    raise _ex

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
      requests.get(pathlib.Path(base_url) / 'api/v1/enqueue', params=dict(url=url, body=body))

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
  webserver_p.add_argument('--base-url',
                           help='The base URL')

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
    mp3dir.mkdir(parents=True, exist_ok=True)
    init_db(pq, db_session.bind)
    return

  queue = pq['articles']

  if cmd == 'converter':
    while True:
      with db_session.begin():
        task = queue.get()
        error_tb = None
        if task is not None:
          article = db_session.query(Article).get(task.data['article_id'])
          log.info('processing %s', article.url)
          enhanced = bool(task.data.get('enhanced'))
          outpath = enhanced_mp3_path(article) if enhanced else mp3path(article)
          try:
            log.info('creating queue')
            subprocess_queue = Queue()
            log.info('creating process')
            process = Process(target=worker, args=(subprocess_queue, article, enhanced, outpath))
            log.info('starting process')
            process.start()
            log.info('joining process')
            process.join()
            log.info('joined process')
            if process.exitcode != 0:
              raise Exception('got exit code ' + str(process.exitcode))
            result, error_tb = subprocess_queue.get()
            assert bool(result) or bool(error_tb)
            if error_tb:
              raise TunneledException()
            else:
              if article.body is None:
                article.title, article.body = result
          # Include OSError to report things like OOMs when forking a process.
          except TunneledException as ex:
            log.exception('error processing article')
            subj = 'AudioLizard | Error processing article'
            assert error_tb is not None
            msg = '\n\n'.join([article.url, error_tb, article.body or ''])
          except (OSError, Exception) as ex:
            log.exception('error processing article')
            subj = 'AudioLizard | Error processing article'
            msg = '\n\n'.join([article.url, traceback.format_exc(), article.body or ''])
          else:
            article.converted = datetime.now()
            subj = 'AudioLizard | %s' % article.title or article.url
            mp3_url = pathlib.Path(cfg.base_url) / 'mp3' / str(article.id) if cfg.base_url else ''
            enhance_url = pathlib.Path(cfg.base_url) / 'mp3' / str(article.id) / 'enhance' if cfg.base_url else ''
            msg = '\n\n'.join(filter(None, map(str, [article.title or '', article.url, mp3_url, enhance_url, article.body or ''])))
          if cfg.to:
            msg = MIMEText(msg, 'plain', 'utf-8')
            msg['Subject'] = subj
            msg['To'] = cfg.to
            msg['From'] = getattr(cfg, 'from')
            print(
              subj
            )
            s = SMTP('localhost')
            s.sendmail(getattr(cfg, 'from'), [cfg.to], msg.as_string())
            s.quit()
  elif cmd == 'webserver':
    app.config['CORS_HEADERS'] = 'Content-Type'
    if cfg.secret: app.config['secret'] = cfg.secret
    if cfg.base_url: app.config['base_url'] = cfg.base_url or 'https://example.com'
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


def worker(q, article, enhanced, outpath):
  try:
    if article.body is not None:
      q.put((convert_text(None, article.body, outpath, enhanced), None))
    else:
      q.put((convert(article.url, outpath, enhanced), None))
  except Exception as exc:
    log.error('error processing article', exc_info=exc)
    q.put((None, traceback.format_exc()))
