<!-- @format -->

# AudioLizard

This is a REST API server that speech-synthesizes articles (stripping boilerplate, doing sentence segmentation, etc.) into a podcast feed of MP3s which you can consume with your favorite podcasting app. You can feed in articles via bookmarklet or [PWA].

The PWA is available at <https://github.com/yang/audiolizard-pwa>.

## Installation

### Ubuntu 18.04

Install prerequisites on Ubuntu:

    sudo apt-get install \
      postgresql-10 \
      ffmpeg \
      ubuntu-restricted-extras \
      postgresql-server-dev-10 \
      build-essential \
      python-dev \
      postfix \
      openjdk-11-jdk # Needed for boilerpipe
    sudo snap install google-cloud-sdk  --classic

Recommended: set up postfix to send via gmail:

    https://www.howtoforge.com/tutorial/configure-postfix-to-use-gmail-as-a-mail-relay/

### OS X

Install prerequisites on OS X:

    sudo port install postgresql95-server postgresql95 openjdk10

### All platforms

In this source dir, install the application (e.g. into a virtualenv):

    pip install --process-dependency-links -e .

Download nltk data:

    python -c 'import nltk; nltk.download()'

Create the necessary postgresql user and DB (and store the password in your [pgpass file]):

    sudo -u postgres createuser -P webreader
    sudo -u postgres createdb -O webreader webreader
    touch ~/.pgpass
    chmod 600 ~/.pgpass

    cat >> ~/.pgpass
    localhost:5432:webreader:webreader:PASSWORD
    ^D

    psql -h localhost -U webreader webreader
    create table articles (id serial primary key, url text, created timestamp not null, title text, body text, converted timestamp);
    ^D

### Google Cloud Setup

Follow https://cloud.google.com/text-to-speech/docs/quickstart-client-libraries.

Configure the `GOOGLE_APPLICATION_CREDENTIALS` env var as described, pointing to your json credentials.
You may want to put this in a `.mise.local.toml`.

Make sure the `gcloud` command works!
And try it out with:

    web-reader convert http://example.com out.mp3

## Usage

### Converting Single Document

Run:

    web-reader convert URL OUTMP3

For example:

    web-reader convert https://medium.com/@AnandWrites/209ffc24ab90 aspen.mp3

You can also select a local plain-text file:

    web-reader convert-file ~/Documents/article.txt article.mp3

### Basic App Server

One-time: run `web-reader init` to set up the DB and MP3 dir.

Run the web server with just `web-reader webserver`.

Run the converter daemon with `web-reader converter`.

To set up Google TTS API auth, run with the appropriate environment, e.g.:

    GOOGLE_APPLICATION_CREDENTIALS=... web-reader converter

Try submitting a web page with <http://localhost:5000/api/v1/enqueue?url=SOMEURL>.

For normal on-going use, you can use a handy bookmarklet for one-click submission of your current page. It tries to extract the main body content by default, but you can also just have some text on the page already selected when you press the bookmarklet to process just that selection:

    javascript:var r=new XMLHttpRequest();try{r.open('POST','http://localhost:5000/api/v1/enqueue',false);r.setRequestHeader("Content-Type", "application/json;charset=UTF-8");r.send(JSON.stringify({url:document.location.href,body:window.getSelection?window.getSelection().toString():document.selection.createRange().text}));alert('done');}catch(e){alert('failed');}

Finally, you can reap the fruits by subscribing to the output Podcast RSS feed with <http://localhost:5000/feed>.

In all of the above, you should replace `localhost:5000` with whatever final server you're hosting on.

### Extended App Server Setup

To make your app server run at system startup, you can use something like the following Upstart scripts:

https://github.com/yang/personal-cm/tree/master/roles/webreader/etc/init

To make your web server accessible outside your firewall, [localtunnel] is a quick solution.

[localtunnel]: http://localtunnel.me/

### Resubmitting Old Failed Articles

You can use the `reconvert` sub-command to force certain article IDs to get
re-processed. This will just produce new MP3s in place. No new feed entries
are created. Text won't be re-extracted - only the audio synthesis is rerun.

You can also use the `resubmit` sub-command to retry submitting old articles that
failed. This will create new feed entries (and won't affect old entries).
For instance,

    web-reader resubmit http://localhost:5000 -d 2015-11-01 -o oldest -n 20

will resubmit to the AudioLizard web server running on port 5000 all the URLs
that were _ever only_ failures (i.e., ignore failures that got converted
successfully another time), limited to a batch of 20 (starting with the oldest
first).

This logic only considers distinct URLs as candidates for resubmission, which
is usually the correct behavior.

## How It Works

1. Enqueue the web article URL into a [PQ] queue.
2. Fetch the page.
3. Extract the main body content (remove boilerplate) using trafilatura.
4. Clean up the text with [ftfy].
5. Split up text into sentences (iSpeech API only takes small chunks of text) using [nltk] sentence segmentation.
6. Submit each sentence to the Google Cloud TTS API to get back many MP3s.
7. Combine the MP3s (with 500ms of silence in between sentences) using [pydub].
8. Generate the podcast feed with [feedgen].

[pq]: https://github.com/malthe/pq/
[ftfy]: https://github.com/LuminosoInsight/python-ftfy
[nltk]: http://www.nltk.org/
[pydub]: http://pydub.com/
[feedgen]: https://github.com/lkiesow/python-feedgen
[pgdg]: https://wiki.postgresql.org/wiki/Apt
[pgpass file]: http://www.postgresql.org/docs/9.3/static/libpq-pgpass.html
[pwa]: https://github.com/yang/audiolizard-pwa
