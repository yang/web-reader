# -*- coding: utf-8 -*-

"""
SoundGecko clone - a web app to convert web pages to an MP3 podcast feed.
"""

from argparse import ArgumentParser

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
import sqlalchemy as sa
from sqlalchemy.engine import create_engine
from sqlalchemy.ext.declarative.api import declarative_base
from sqlalchemy.orm.scoping import scoped_session
from sqlalchemy.orm.session import sessionmaker
from flask.ext.cors import cross_origin
import time

__author__ = 'yang'

UA = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36'

app = flask.Flask(__name__)

log = logging.getLogger(__name__)

sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
goose = Goose()

alpha = re.compile(r'[a-z]', re.IGNORECASE)
newlines = re.compile(r'\n+')

engine = None
db_session = None
pq = None
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
  with db_session.begin():
    import pprint; pprint.pprint(data)
    url = data.get('url')
    body = data.get('body') or None
    article = Article(url=url, body=body, created=datetime.now())
    db_session.add(article)
    db_session.flush()
  queue.put(dict(article_id=article.id))
  return flask.jsonify(done=True)

@app.route('/feed')
def feed():
  limit = int(request.args.get('limit', 30))
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
      fe.title(article.title or article.body[:100])
      fe.description(article.body)
      fe.link(href=article.url)
      fe.enclosure('http://yz.mit.edu/audiolizard/mp3/%s' % article.id, 0, 'audio/mpeg')
    return flask.Response(fg.rss_str(pretty=True), mimetype='application/rss+xml')

@app.route('/mp3/<int:article_id>')
def mp3(article_id):
  with db_session.begin():
    article = db_session.query(Article).get(article_id)
    with open(mp3path(article)) as f:
      return flask.Response(f.read(), mimetype='audio/mpeg')

def convert(url, outpath):
  resp = requests.get(url, verify=False, headers={'user-agent': UA}, timeout=30)
  ":type: requests.Response"

  raw_title, raw_text = extract(resp.content)
  text = ftfy.fix_text(raw_text)
  title = ftfy.fix_text(raw_title) if len(raw_title.strip()) > 0 else ''

  return convert_text(title, text, outpath)

splitters = [
    re.compile(r'[:;]| -+ |\.{2,}|--+|—'),
    re.compile(r'[,]'),
]

# Experimentally determined that the API tolerates up to 250 words, where words
# are any non-white-space text.  Hence, "hello . world ." is 4 words, and
# "hello-world" is one word.
def segments(sent):
  if len(sent.split()) <= 250:
    yield sent
  else:
    splits = splitters[0].split(sent)
    for split in splits:
      if len(split.split()) <= 250:
        yield split
      else:
        yield 'warning: audio lizard phrase too long'

def convert_text(title, text, outpath):
  segs = [
    seg
    for par in [title] + newlines.split(text.strip())
    if par and alpha.search(par)
    for sent in sent_detector.tokenize(par.strip())
    if alpha.search(sent)
    for seg in segments(sent)
    if alpha.search(seg)
  ]

  tempdir = path.path(tempfile.mkdtemp('web-reader'))
  ":type: path.Path"

  log.info('spooling %s sentences to temp dir %s', len(segs), tempdir)
  for i, seg in enumerate(segs):
    params = dict(
      format='mp3',
      action='convert',
      apikey='59e482ac28dd52db23a22aff4ac1d31e',
      speed='0',
      voice='usenglishfemale',
      text=seg
    )
    for trial in xrange(3):
      try:
        resp = requests.get('http://api.ispeech.org/api/rest', params=params, timeout=30)
        ":type: requests.Response"
      except:
        log.warn('used trial #%s of 3 on text: %s', trial + 1, seg)
        a,b,c = sys.exc_info()
        if trial + 1 < 3: time.sleep(5)
      else:
        break
    else:
      raise a,b,c
    (tempdir / ('%s.mp3' % i)).write_bytes(resp.content)

  combined = reduce(
    lambda x,y: x + pydub.AudioSegment.silent(500) + y,
    (pydub.AudioSegment.from_mp3(tempdir / ('%s.mp3' % i)) for i in xrange(len(segs)))
  )
  combined = combined + pydub.AudioSegment.silent(2000)
  ":type: pydub.AudioSegment"
  combined.export(outpath, format='mp3')

  return title, text

def init_db():
  Base.metadata.drop_all(bind=engine)
  Base.metadata.create_all(bind=engine)
  pq.create()

def mp3path(article):
  return mp3dir / ('%s.mp3' % article.id)

def main(argv=sys.argv):
  global engine, db_session, pq, queue

  logging.basicConfig()
  log.setLevel(logging.INFO)

  p = ArgumentParser(description=__doc__)
  subparsers = p.add_subparsers(help='sub-command help', dest='cmd')
  init_p = subparsers.add_parser('init')
  converter_p = subparsers.add_parser('converter')
  webserver_p = subparsers.add_parser('webserver')

  webserver_p.add_argument('-p', '--port', type=int,
                           help='Web server listen port')

  converter_p.add_argument('-t', '--to',
                           help='Email to send notifications to (sent only if this is set)')
  converter_p.add_argument('-f', '--from', default='audiolizard@' + socket.getfqdn(),
                           help='Email to send notifications as')

  cfg = p.parse_args(argv[1:])
  cmd = cfg.cmd

  log.info('command-line config: %r', cfg)

  engine = create_engine('postgresql://webreader@localhost/webreader')
  pq = PQ(engine.raw_connection())
  db_session = scoped_session(sessionmaker(autocommit=True,
                                           autoflush=False,
                                           bind=engine))

  if cmd == 'init':
    mp3dir.makedirs_p()
    init_db()
    return

  queue = pq['articles']

  if cmd == 'converter':
    while True:
      with db_session.begin():
        task = queue.get()
        if task is not None:
          article = db_session.query(Article).get(task.data['article_id'])
          log.info('processing %s', article.url)
          msg = ''
          try:
            if article.body is not None:
              convert_text(None, article.body, mp3path(article))
            else:
              article.title, article.body = convert(article.url, mp3path(article))
          except Exception as ex:
            log.exception('error processing article')
            subj = 'AudioLizard | Error processing article'
            msg = '\n\n'.join([article.url, traceback.format_exc(), article.body or ''])
          else:
            article.converted = datetime.now()
            subj = 'AudioLizard | %s' % article.title or article.url
            msg = '\n\n'.join([article.title or '', article.url, article.body or ''])
          finally:
            if cfg.to:
              msg = MIMEText(msg, 'plain', 'utf-8')
              msg['Subject'] = subj
              msg['To'] = cfg.to
              msg['From'] = getattr(cfg, 'from')
              s = SMTP('localhost')
              s.sendmail(getattr(cfg, 'from'), [cfg.to], msg.as_string())
              s.quit()
  elif cmd == 'webserver':
    app.config['CORS_HEADERS'] = 'Content-Type'
    app.run(port=cfg.port)
  else:
    raise Exception()
