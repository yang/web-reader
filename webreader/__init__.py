# -*- coding: utf-8 -*-

from datetime import datetime
import logging
import re
import sys
import tempfile
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

  extracted = goose.extract(raw_html=html)
  gs_text = extracted.cleaned_text
  title = extracted.title

  return title, bp_text if len(bp_text) > len(gs_text) else gs_text

@app.route('/api/v1/enqueue')
def enqueue():
  with db_session.begin():
    url = request.args.get('url')
    body = request.args.get('body') or None
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
  resp = requests.get(url, verify=False, headers={'user-agent': UA})
  ":type: requests.Response"

  raw_title, raw_text = extract(resp.content)
  text = ftfy.fix_text(raw_text)
  title = ftfy.fix_text(raw_title)

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
    resp = requests.get('http://api.ispeech.org/api/rest', params=params)
    ":type: requests.Response"
    (tempdir / ('%s.mp3' % i)).write_bytes(resp.content)

  combined = reduce(
    lambda x,y: x + pydub.AudioSegment.silent(500) + y,
    (pydub.AudioSegment.from_mp3(tempdir / ('%s.mp3' % i)) for i in xrange(len(segs)))
  )
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
  engine = create_engine('postgresql://webreader@localhost/webreader')
  pq = PQ(engine.raw_connection())
  db_session = scoped_session(sessionmaker(autocommit=True,
                                           autoflush=False,
                                           bind=engine))
  cmd = 'webserver'
  if len(argv) > 1:
    cmd = argv[1]

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
          if article.body is not None:
            convert_text(None, article.body, mp3path(article))
          else:
            article.title, article.body = convert(article.url, mp3path(article))
          article.converted = datetime.now()
  elif cmd == 'webserver':
    port = int(argv[2]) if len(argv) > 2 else None
    app.run(port=port)
  else:
    raise Exception()
