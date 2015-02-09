from distutils.core import setup

setup(
  name='web-reader',
  version='1.0',
  packages=['webreader'],
  url='https://github.com/yang/web-reader',
  license='AGPL',
  author='yang',
  author_email='',
  entry_points={
    'console_scripts': [
      'web-reader = webreader:main',
    ]
  },
  description='Simple SoundGecko replacement', requires=['requests', 'flask', 'nltk', 'pydub', 'boilerpipe', 'path.py',
                                                         'goose-extractor', 'ftfy', 'sqlalchemy', 'psycopg2']
)
