"""Build an English word-frequency vocabulary from a Wikipedia multistream
*index* file (enwiki-latest-pages-articles-multistream-index.txt.bz2).

The index only contains page TITLES, so this yields a title-derived vocabulary.
Streams the bz2 line by line -> constant memory except for the word counter.
"""
from __future__ import annotations
import bz2
import re
from collections import Counter

# Tokenizer: ASCII letters only, length >= 2, lowercased.
# Drop anything containing a digit (e.g. "covid", "19" -> only keep "covid"? no:
# the digit token "19" is dropped; "covid" kept because we split on non-letters).
_WORD_RE = re.compile(r"[a-z]{2,}")

# Wikipedia "namespace" titles we usually don't want as vocabulary
# (Talk:, Category:, Template:, Wikipedia:, File:, Help:, Portal:, etc.)
_NAMESPACE_RE = re.compile(
    r"^(Talk|User|Wikipedia|File|MediaWiki|Template|Help|Category|Portal|Draft|"
    r"Module|Book|TimedText):",
    re.IGNORECASE,
)


def iter_titles(index_path: str):
    """Yield the title from each line of the (bz2) index file."""
    with bz2.open(index_path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # offset:page_id:title  -> keep title intact even if it has colons
            parts = line.rstrip("\n").split(":", 2)
            if len(parts) == 3:
                yield parts[2]


def build_vocab(index_path: str, skip_namespaces: bool = True) -> Counter:
    counts: Counter[str] = Counter()
    for title in iter_titles(index_path):
        if skip_namespaces and _NAMESPACE_RE.match(title):
            continue
        for tok in _WORD_RE.findall(title.lower()):
            counts[tok] += 1
    return counts


def vocab_above(counts: Counter, min_count: int = 1) -> set[str]:
    """Set of words seen at least `min_count` times (noise filter)."""
    return {w for w, c in counts.items() if c >= min_count}


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "sample-index.txt.bz2"
    counts = build_vocab(path)
    print("unique words:", len(counts))
    print("top 10:", counts.most_common(10))
