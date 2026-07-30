"""Deterministic Unicode → ASCII normalization for descriptor artifacts.

NiWrap codegens wrappers in many languages and the descriptors pass through many
tools, so we keep descriptor text effectively ASCII. Source help text, however,
routinely carries Unicode punctuation (em-dashes, curly quotes, ellipses) — and
third-party repos aren't uniformly UTF-8. We handle that in two deterministic,
idempotent steps applied at the file-read boundary, so everything downstream
(Explorer reports, descriptors, generated wrappers) is ASCII:

- ``decode_source`` recovers the intended character from bytes (UTF-8, then a
  cp1252 fallback that never fails), instead of emitting U+FFFD.
- ``to_ascii`` transliterates common punctuation to ASCII, folds accents, and
  drops any residual non-ASCII.

This is deliberately not an LLM concern: an already-corrupted character can't be
recovered by a model, and deterministic transliteration is reliable and idempotent
where a prompt instruction is neither.
"""

from __future__ import annotations

import unicodedata

# Curated punctuation → ASCII. Only characters whose ASCII intent is unambiguous;
# anything else falls through to accent-folding / dropping.
_PUNCT: dict[str, str] = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",  # hyphen/figure/en dashes
    "—": "-", "―": "-", "−": "-",  # em/bar dashes, minus sign
    "‘": "'", "’": "'", "‚": "'", "‛": "'",  # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',  # double quotes
    "…": "...",  # horizontal ellipsis
    "•": "*", "·": "*",  # bullet / middle dot
    " ": " ", " ": " ", " ": " ", " ": " ",  # non-breaking / thin spaces
    "×": "x",  # multiplication sign (e.g. dimension "2 × 3")
}
_TABLE = {ord(k): v for k, v in _PUNCT.items()}


def to_ascii(text: str) -> str:
    """Transliterate ``text`` to ASCII, deterministically and idempotently.

    ASCII input is returned unchanged (fast path). Otherwise: map known
    punctuation, fold accents via compatibility decomposition (``é`` → ``e``),
    then drop any remaining non-ASCII.
    """
    if text.isascii():
        return text
    text = text.translate(_TABLE)
    if text.isascii():
        return text
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.encode("ascii", "ignore").decode("ascii")


def decode_source(data: bytes) -> str:
    """Decode source-file bytes: UTF-8, falling back to cp1252.

    Third-party source repos are not uniformly UTF-8 — Windows-authored files use
    cp1252 for punctuation like em-dashes (byte ``0x97``), which is invalid UTF-8.
    cp1252 decodes every byte, so the fallback never fails and recovers the
    intended character instead of the U+FFFD replacement char that strict UTF-8
    ``errors="replace"`` would emit.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
