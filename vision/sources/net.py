"""Downloads: one HTTP client, and reading a .zst file from the web as a stream of text lines.

The Lichess open database files are huge (a month of games is tens of GB); only their beginning is read and the
connection is closed once enough is taken. compression.zstd is in the standard library since Python 3.14.
"""

import codecs
from collections.abc import Iterator

import httpx
from compression import zstd

HEADERS = {"User-Agent": "board-vision/0.1 (local training app)"}


def client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=httpx.Timeout(60, connect=20), follow_redirects=True)


def zst_lines(url: str) -> Iterator[str]:
    """The lines of a remote .zst text file, as they arrive; stop iterating to stop the download."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with client() as c, c.stream("GET", url) as r:
        r.raise_for_status()
        dec = zstd.ZstdDecompressor()
        rest = ""
        for chunk in r.iter_bytes():
            data = chunk
            while data:
                text = decoder.decode(dec.decompress(data))
                if dec.eof:  # a file of several zstd frames: go on with the next one
                    data = dec.unused_data
                    dec = zstd.ZstdDecompressor()
                else:
                    data = b""
                if text:
                    rest += text
                    *lines, rest = rest.split("\n")
                    yield from lines
        if rest:
            yield rest
