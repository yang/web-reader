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
    'feedgen==0.9.0',
    'flask==2.3.2',
    'flask-cors==3.0.10',
    'ftfy==3.4.0',
    'nltk==3.7',
    'path.py==7.2',
    'pq==1.9.0',
    'psycopg2==2.9.5',
    # The 'security' extra is to deal with SSL errors.  See
    # <http://stackoverflow.com/a/30438722/43118>.
    'requests[security]==2.28.1',
    'sqlalchemy==1.4.44',
    'python-slugify==1.1.4',
    'trafilatura==1.4.0'
  ],
  dependency_links=[
    'https://github.com/migomhmi/python-boilerpipe/archive/master.zip#egg=boilerpipe-1.3.0.0',
  ],
)

