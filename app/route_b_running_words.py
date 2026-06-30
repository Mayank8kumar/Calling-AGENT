"""ROUTE B: true running-word frequencies from the CONTENT dump.

Requires BOTH files in the same folder:
  enwiki-latest-pages-articles-multistream.xml.bz2          (~20 GB)
  enwiki-latest-pages-articles-multistream-index.txt.bz2    (the index)

The index gives the byte offset of each 100-page bz2 stream. We seek to each
unique offset, decompress that one stream, strip wiki markup, and count words.
This yields real corpus frequencies (verbs, function words, etc.) that the
title-only Route A under-represents.
"""
from __future__ import annotations
import bz2
import re
from collections import Counter

_WORD_RE = re.compile(r"[a-z]{2,}")
_TAG_RE = re.compile(r"<[^>]+>")             # xml tags
_MARKUP_RE = re.compile(r"\{\{[^}]*\}\}|\[\[|\]\]|'''?|==+")  # rough wikitext


def unique_offsets(index_path: str) -> list[int]:
    """Distinct stream start offsets, in order (one per 100-page stream)."""
    offsets, last = [], -1
    with bz2.open(index_path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            off = int(line.split(":", 1)[0])
            if off != last:
                offsets.append(off)
                last = off
    return offsets


def iter_stream_text(xml_bz2_path: str, offsets: list[int]):
    """Yield decompressed XML text for each stream, one stream at a time."""
    offsets = sorted(set(offsets)) + [None]  # None -> read to EOF for last
    with open(xml_bz2_path, "rb") as f:
        for start, nxt in zip(offsets, offsets[1:]):
            f.seek(start)
            blob = f.read(nxt - start) if nxt is not None else f.read()
            yield bz2.BZ2Decompressor().decompress(blob).decode(
                "utf-8", errors="replace"
            )


def count_running_words(xml_bz2_path: str, index_path: str,
                        max_streams: int | None = None) -> Counter:
    counts: Counter[str] = Counter()
    offsets = unique_offsets(index_path)
    if max_streams:                       # sample a subset while developing
        offsets = offsets[:max_streams]
    for xml in iter_stream_text(xml_bz2_path, offsets):
        text = _MARKUP_RE.sub(" ", _TAG_RE.sub(" ", xml)).lower()
        counts.update(_WORD_RE.findall(text))
    return counts


if __name__ == "__main__":
    # Start with a few streams to sanity-check before the full ~200k-stream run.
    c = count_running_words(
        "enwiki-latest-pages-articles-multistream.xml.bz2",
        "enwiki-latest-pages-articles-multistream-index.txt.bz2",
        max_streams=5,
    )
    print(c.most_common(20))
