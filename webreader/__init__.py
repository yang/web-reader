import re
import sys
import itertools
import tempfile
import nltk
import requests
import flask
from flask import request
from boilerpipe.extract import Extractor
import pydub
import path
from goose import Goose
import ftfy
import sqlalchemy
import psycopg2

__author__ = 'yang'

app = flask.Flask(__name__)

sent_detector = nltk.data.load('tokenizers/punkt/english.pickle')
goose = Goose()

alpha = re.compile(r'[a-z]', re.IGNORECASE)
newlines = re.compile(r'\n+')

def swallow(f):
  try: return f()
  except: return None

def extract(html):
  extractor = Extractor(extractor='ArticleExtractor', html=html)
  bp_text = extractor.getText()

  extracted = goose.extract(raw_html=html)
  gs_text = extracted.cleaned_text
  title = extracted.title

  return title, bp_text if len(bp_text) > len(gs_text) else gs_text

@app.route('/api/v1/convert')
def convert():
  url = request.args['url']
  resp = requests.get(url)
  ":type: requests.Response"

  raw_title, raw_text = extract(resp.content)
  text = ftfy.fix_text(raw_text)
  title = ftfy.fix_text(raw_title)

  sents = [title] + list(itertools.chain.from_iterable(
    sent_detector.tokenize(par.strip())
    for par in newlines.split(text.strip()) if alpha.search(par)
  ))

  tempdir = path.path(tempfile.mkdtemp('web-reader'))
  ":type: path.Path"

  app.logger.debug('spooling %s sentences to temp dir %s', len(sents), tempdir)
  for i, sent in enumerate(sents):
    params = dict(
      format='mp3',
      action='convert',
      apikey='59e482ac28dd52db23a22aff4ac1d31e',
      speed='0',
      voice='usenglishfemale',
      text=sent
    )
    resp = requests.get('http://api.ispeech.org/api/rest', params=params)
    ":type: requests.Response"
    (tempdir / ('%s.mp3' % i)).write_bytes(resp.content)

  combined = reduce(
    lambda x,y: x + pydub.AudioSegment.silent(500) + y,
    (pydub.AudioSegment.from_mp3(tempdir / ('%s.mp3' % i)) for i in xrange(len(sents)))
  )
  ":type: pydub.AudioSegment"
  combined_mp3 = tempdir / 'combined.mp3'
  combined.export(combined_mp3, format='mp3')
  with open(combined_mp3) as f:
    return flask.Response(f.read(), mimetype='audio/mpeg')

def main(argv=sys.argv):
  app.run(debug=True)