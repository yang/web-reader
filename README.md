# AudioLizard

A quick-and-dirty SoundGecko replacement!

## Usage

One-time: run `web-reader init` to set up the DB and MP3 dir.

Run the web server with just `web-reader`.

Submit a web page with http://localhost:5000/api/v1/enqueue?url=SOMEURL.

Run the converter daemon with `web-reader converter`.

Check the output Podcast RSS feed with http://localhost:5000/feed.