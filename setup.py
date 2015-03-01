from setuptools import setup

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
  description='Simple SoundGecko replacement',
  install_requires=[
    'boilerpipe==1.2.0.0',
    'feedgen==0.3.1',
    'flask==0.10.1',
    'ftfy==3.4.0',
    'goose-extractor==1.0.25',
    'nltk==3.0.1',
    'path.py==7.2',
    'pq==1.2',
    'psycopg2==2.6',
    'pydub==0.10.0',
    'requests==2.5.3',
    'sqlalchemy==0.9.8',
  ]
)

