"""Check the tokens used in Fluent settings-API paths against an English
vocabulary derived from the Wikipedia index.

Flags tokens that don't appear in the reference vocab -> candidates for typos
or domain jargon that may want an allowlist entry.
"""
from __future__ import annotations
import re

# split a settings path into lowercase word tokens.
# handles "/", "-", "_", and camelCase boundaries.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT = re.compile(r"[/\-_\s]+")


def tokenize_path(path: str) -> list[str]:
    out: list[str] = []
    for seg in _SPLIT.split(path.strip("/")):
        if not seg:
            continue
        for piece in _CAMEL.split(seg):
            piece = re.sub(r"[^a-zA-Z]", "", piece).lower()
            if len(piece) >= 2:
                out.append(piece)
    return out


# Known CFD/abbreviation terms that legitimately aren't English dictionary words
DOMAIN_ALLOWLIST = {
    "bc", "vof", "les", "rans", "udf", "tui", "init", "kw", "ke",
    "sst", "wall", "mesh", "solver", "fluent",
}


def check_paths(paths: list[str], vocab: set[str]) -> dict[str, list[str]]:
    """Return {path: [unknown_tokens]} for paths with unknown tokens."""
    flagged: dict[str, list[str]] = {}
    for p in paths:
        unknown = [
            t for t in tokenize_path(p)
            if t not in vocab and t not in DOMAIN_ALLOWLIST
        ]
        if unknown:
            flagged[p] = unknown
    return flagged


if __name__ == "__main__":
    from wiki_index_vocab import build_vocab, vocab_above

    vocab = vocab_above(build_vocab("sample-index.txt.bz2"), min_count=1)

    # In real use, pull these from the settings tree / REST static-info
    # (e.g. client.get_static_info()) instead of hardcoding.
    sample_paths = [
        "setup/models/viscous/model",
        "setup/models/energy/enabled",
        "setup/boundary-conditions/velocity-inlet",
        "solution/initialization/hybrid-initialize",
        "setup/models/viscious/model",      # <- deliberate typo: "viscious"
        "results/graphics/contuors",        # <- deliberate typo: "contuors"
    ]
    flagged = check_paths(sample_paths, vocab)
    for path, unknown in flagged.items():
        print(f"{path}\n    unknown tokens: {unknown}")
