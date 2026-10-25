"""Text helpers shared by every stage: normalisation, sentence splitting, hashing."""

import hashlib
import re
import unicodedata

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_SPACES = re.compile(r"\s+")
# Split after . ! ? when followed by a capital letter, digit or opening bracket.
# Common abbreviations are protected first so "e.g. EGFR" stays in one sentence.
_ABBREV = ("e.g.", "i.e.", "et al.", "vs.", "approx.", "Fig.", "no.", "No.")
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?")


def normalise(text: str) -> str:
    """Canonical form for comparisons: NFKC, plain hyphens, single spaces."""
    text = unicodedata.normalize("NFKC", text or "").translate(_DASHES)
    return _SPACES.sub(" ", text).strip()


def split_sentences(text: str) -> list[str]:
    text = normalise(text)
    if not text:
        return []
    guarded = text
    for i, abbr in enumerate(_ABBREV):
        guarded = guarded.replace(abbr, f"\x00{i}\x00")
    parts = _SENT_END.split(guarded)
    out = []
    for part in parts:
        for i, abbr in enumerate(_ABBREV):
            part = part.replace(f"\x00{i}\x00", abbr)
        part = part.strip()
        if part:
            out.append(part)
    return out


def numbers_in(text: str) -> list[str]:
    """All numbers in a text, with decimal commas turned into points."""
    return [n.replace(",", ".") for n in _NUMBER.findall(normalise(text))]


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def short_hash(text: str) -> str:
    return sha256(text)[:12]
