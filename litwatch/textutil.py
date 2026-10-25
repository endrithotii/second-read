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


_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_NUMBER_WORD = re.compile(
    r"\b(?:(" + "|".join(_TENS) + r")(?:[- ](" + "|".join(_UNITS[1:10]) + r"))?|(" + "|".join(_UNITS) + r"))\b",
    re.I)


def _word_to_digits(m: re.Match) -> str:
    tens, unit, single = m.group(1), m.group(2), m.group(3)
    if single:
        return str(_UNITS.index(single.lower()))
    return str(_TENS[tens.lower()] + (_UNITS.index(unit.lower()) if unit else 0))


def numbers_in(text: str) -> list[str]:
    """All numbers in a text, with decimal commas turned into points and number
    words read as digits, so "Thirty-one patients" contains 31."""
    text = _NUMBER_WORD.sub(_word_to_digits, normalise(text))
    return [n.replace(",", ".") for n in _NUMBER.findall(text)]


_DEFINITION = re.compile(r"((?:[A-Za-z][\w-]*\s+){0,5}[A-Za-z][\w-]*)\s*\(([A-Za-z][A-Za-z0-9-]{1,9})\)")


def abbreviations(text: str) -> dict[str, str]:
    """Abbreviations the text defines itself, as written: 'pirfenidone (PFD)' -> {'pfd': 'pirfenidone'}.
    The long form is trimmed to the words whose initials spell the abbreviation."""
    out = {}
    for long, abbr in _DEFINITION.findall(normalise(text)):
        words = long.split()
        letters = re.sub(r"[^A-Za-z]", "", abbr).lower()
        n = next((k for k in range(1, len(words) + 1)
                  if "".join(w[0] for w in words[-k:]).lower() == letters), None)
        if n is None:  # single-word long forms: 'pirfenidone (PFD)'
            last = words[-1].lower()
            if last[0] == letters[0] and all(c in last for c in letters):
                n = 1
        if n:
            out[abbr.lower()] = " ".join(words[-n:]).lower()
    return out


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def short_hash(text: str) -> str:
    return sha256(text)[:12]
