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
    # This hack based on
    # <https://stackoverflow.com/questions/17366784/setuptools-unable-to-use-link-from-dependency-links/17442663#17442663>.
    'boilerpipe<=1.3.0.0',
    'feedgen==0.9.0',
    'flask==1.0.2',
    'flask-cors==3.0.7',
    'ftfy==3.4.0',
    'goose-extractor==1.0.25',
    'nltk==3.6.5',
    'path.py==7.2',
    'pq==1.2',
    'psycopg2==2.7.5',
    'pydub==0.16.5',
    # The 'security' extra is to deal with SSL errors.  See
    # <http://stackoverflow.com/a/30438722/43118>.
    'requests[security]==2.20.0',
    'sqlalchemy==1.4.2',
    'python-slugify==1.1.4',
  ],
  dependency_links=[
    'https://github.com/migomhmi/python-boilerpipe/archive/master.zip#egg=boilerpipe-1.3.0.0',
  ],
)

