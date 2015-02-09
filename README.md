# AudioLizard

A quick-and-dirty SoundGecko replacement!

## Usage

One-time: run `web-reader init` to set up the DB and MP3 dir.

Run the web server with just `web-reader`.

Submit a web page with <http://localhost:5000/api/v1/enqueue?url=SOMEURL>.

Run the converter daemon with `web-reader converter`.

Check the output Podcast RSS feed with <http://localhost:5000/feed>.

## How It Works

1. Enqueue the web article URL into a [PQ] queue.
2. Fetch the page.
3. Extract the main body content (remove boilerplate) using both [goose] and [boilerpipe], whichever recalls more.
4. Clean up the text with [ftfy].
5. Split up text into sentences (iSpeech API only takes small chunks of text) using [nltk] sentence segmentation.
6. Submit each sentence to the [iSpeech] API to get back many MP3s.
7. Combine the MP3s (with 500ms of silence in between sentences) using [pydub].
8. Generate the podcast feed with [feedgen].

[PQ]: https://github.com/malthe/pq/
[goose]: https://github.com/GravityLabs/goose
[boilerpipe]: https://code.google.com/p/boilerpipe/
[ftfy]: https://github.com/LuminosoInsight/python-ftfy
[nltk]: http://www.nltk.org/
[iSpeech]: http://www.ispeech.org/
[pydub]: http://pydub.com/
[feedgen]: https://github.com/lkiesow/python-feedgen