"""Stages 2 to 6: screen, extract, summarise, verify, route.

The rule throughout: a model proposes, code decides. Models answer narrow
questions and must point to a numbered sentence; the decisions themselves
(include or exclude, accept or send to a reviewer) are computed here from
those answers and from checks that cannot hallucinate.
"""

from __future__ import annotations

import json
import re

from .llm import Model
from .pubmed import Paper
from .textutil import normalise, numbers_in, short_hash

# ---------------------------------------------------------------- prompts --

SCREEN_SYSTEM = """You screen biomedical abstracts for a literature surveillance team.
Answer each criterion separately with "yes", "no" or "unclear".
For every "yes" or "no", give the number of the sentence that supports your answer.
Use only what the title and abstract say. Do not use outside knowledge.
If the abstract does not say enough to decide, answer "unclear".
Reply with JSON only: {"criteria": [{"id": "...", "answer": "yes|no|unclear", "sentence": <int or null>}]}"""

EXTRACT_SYSTEM = """You extract facts from one biomedical abstract into fixed fields.
For each field give:
  "value": the fact, as close to the paper's own wording as possible, or null if the abstract does not state it;
  "sentence": the number of the sentence it comes from;
  "quote": the exact words copied from that sentence that state the fact.
Never infer, never combine facts from outside the abstract, never fill a field the abstract does not support.
Reply with JSON only: {"fields": {"<field id>": {"value": ..., "sentence": ..., "quote": ...}}}"""

SUMMARY_SYSTEM = """You write a two-sentence summary for an oncology knowledge base.
You receive structured fields extracted from a paper. You do NOT see the paper.
Use only the facts in the fields. Do not add numbers, drugs, genes or claims that are not in the fields.
Reply with JSON only: {"summary": "..."}"""

CHECK_SYSTEM = """You audit claims about a biomedical abstract. You did not write them.
For each claim, read the numbered sentences and answer one of:
  "supported"     the abstract states this;
  "contradicted"  the abstract states something different;
  "beyond"        the abstract does not state this, though it may be plausible.
Give the number of the sentence you relied on (null for "beyond").
Then list important findings in the abstract that are relevant to the topic but appear in none of the claims.
Use only the abstract. Reply with JSON only:
{"verdicts": [{"claim": <int>, "verdict": "supported|contradicted|beyond", "sentence": <int or null>}], "omitted": ["..."]}"""

PROMPT_VERSIONS = {
    name: short_hash(text) for name, text in
    [("screen", SCREEN_SYSTEM), ("extract", EXTRACT_SYSTEM),
     ("summary", SUMMARY_SYSTEM), ("check", CHECK_SYSTEM)]
}


def numbered(paper: Paper) -> str:
    lines = [f"[0] Title: {paper.title}"]
    lines += [f"[{i}] {s}" for i, s in enumerate(paper.sentences, start=1)]
    return "\n".join(lines)


def as_index(value) -> int | None:
    """Sentence numbers arrive as 5, "5", "[5]" or "S5" depending on the model."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        return int(m.group()) if m else None
    return None


_EMPTY = {"", "null", "none", "n/a", "na", "not stated", "not reported", "unknown", "not applicable"}


def clean_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, list):
        value = "; ".join(map(str, value))
    value = normalise(str(value))
    return None if value.casefold().strip(".") in _EMPTY else value


def sentence_text(paper: Paper, idx) -> str | None:
    idx = as_index(idx)
    if idx is None:
        return None
    if idx == 0:
        return paper.title
    if 1 <= idx <= len(paper.sentences):
        return paper.sentences[idx - 1]
    return None


# --------------------------------------------------------------- screen --

def screen(paper: Paper, topic: dict, model: Model) -> dict:
    if not paper.sentences:
        return {"decision": "review", "reason": "No abstract in PubMed, so it cannot be screened.",
                "criteria": [], "model": None}

    criteria_text = "\n".join(f'- {c["id"]}: {c["text"]}' for c in topic["criteria"])
    user = (f"Topic: {topic['question']}\n\nCriteria:\n{criteria_text}\n\n"
            f"Abstract, sentence by sentence:\n{numbered(paper)}")
    raw = model.complete_json(SCREEN_SYSTEM, user)
    answers = {a.get("id"): a for a in raw.get("criteria", []) if isinstance(a, dict)}

    rows = []
    for c in topic["criteria"]:
        a = answers.get(c["id"], {})
        answer = str(a.get("answer", "unclear")).lower()
        if answer not in ("yes", "no", "unclear"):
            answer = "unclear"
        evidence = sentence_text(paper, a.get("sentence"))
        # A yes or no without a real sentence behind it is not trusted.
        grounded = answer == "unclear" or evidence is not None
        if not grounded:
            answer = "unclear"
        rows.append({"id": c["id"], "required": c["required"], "answer": answer,
                     "sentence": as_index(a.get("sentence")) if evidence else None,
                     "evidence": evidence, "grounded": grounded})

    failed = [r for r in rows if r["required"] and r["answer"] == "no"]
    unclear = [r for r in rows if r["required"] and r["answer"] == "unclear"]
    if failed:
        decision = "exclude"
        reason = "Does not meet: " + ", ".join(r["id"] for r in failed) + "."
    else:
        decision = "include"
        reason = ("Meets all required criteria." if not unclear else
                  "Included because unclear on: " + ", ".join(r["id"] for r in unclear) +
                  ". Unclear counts as include, so nothing relevant is dropped.")
    return {"decision": decision, "reason": reason, "criteria": rows, "model": model.name}


# -------------------------------------------------------------- extract --

def _field_line(f: dict) -> str:
    line = f'- {f["id"]}: {f["text"]}'
    if f.get("kind") == "category":
        line += " Allowed values: " + "; ".join(f["values"]) + ". Give the sentence that justifies it."
    return line


def extract(paper: Paper, topic: dict, model: Model) -> dict:
    fields_text = "\n".join(_field_line(f) for f in topic["fields"])
    user = (f"Fields:\n{fields_text}\n\nAbstract, sentence by sentence:\n{numbered(paper)}")
    raw = model.complete_json(EXTRACT_SYSTEM, user).get("fields", {})
    out = {}
    for f in topic["fields"]:
        item = raw.get(f["id"]) or {}
        if not isinstance(item, dict):
            item = {"value": item}
        value = clean_value(item.get("value"))
        quote = clean_value(item.get("quote"))
        if quote:
            quote = re.sub(r"^\[\d+\]\s*", "", quote)  # models sometimes copy the sentence number
        out[f["id"]] = {"value": value, "sentence": as_index(item.get("sentence")),
                        "quote": quote if value else None}
    return {"fields": out, "model": model.name}


# ------------------------------------------------------------ summarise --

def summarise(record: dict, model: Model) -> dict:
    facts = {k: v["value"] for k, v in record["fields"].items() if v["value"]}
    raw = model.complete_json(SUMMARY_SYSTEM, "Fields:\n" + json.dumps(facts, indent=2))
    return {"text": normalise(raw.get("summary", "")), "model": model.name}


# --------------------------------------------------------------- verify --

_STOPWORDS = {"with", "from", "that", "this", "were", "after", "before", "their", "than",
              "into", "also", "both", "each", "such", "which", "have", "been"}

_ANCHOR = re.compile(r"[A-Za-z]*\d[\w.]*|\b[A-Z]{2,}[A-Za-z0-9]*\b")


def _anchors(value: str) -> list[str]:
    """Tokens a value cannot be right without: anything with a digit, gene-like capitals."""
    return [t.strip(".") for t in _ANCHOR.findall(value or "") if len(t.strip(".")) > 1]


def _acronym_in(token: str, text: str) -> bool:
    """'TKIs' matches 'tyrosine kinase inhibitors': consecutive words whose initials spell it."""
    letters = re.sub(r"[^A-Za-z]", "", token)
    if letters.endswith("s") and letters[:-1].isupper():
        letters = letters[:-1]
    if not (2 <= len(letters) <= 8 and letters.isupper()):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    n = len(letters)
    initials = "".join(w[0] for w in words).upper()
    return letters in initials and any(
        "".join(w[0] for w in words[i:i + n]).upper() == letters for i in range(len(words) - n + 1))


def _quote_in(quote: str, source: str) -> bool:
    """Word for word, allowing '...' between exact pieces, in their original order."""
    pieces = [p.strip() for p in re.split(r"\.\.\.|…", quote) if p.strip()]
    pos, hay = 0, source.casefold()
    for p in pieces:
        found = hay.find(p.casefold(), pos)
        if found == -1:
            return False
        pos = found + len(p)
    return bool(pieces)


def _appears(token: str, text: str, synonyms: dict) -> bool:
    t, hay = token.casefold(), text.casefold()
    if t in hay or _acronym_in(token, text):
        return True
    parts = [p for p in re.split(r"-", token) if p]
    if len(parts) > 1 and all(_appears(p, text, synonyms) for p in parts):
        return True  # "EGFR-TKI" is said by "EGFR tyrosine kinase inhibitors"
    for canonical, alts in synonyms.items():
        group = [canonical, *alts]
        if any(t == g.casefold() for g in group):
            return any(g.casefold() in hay for g in group)
    return False


def code_checks(paper: Paper, extraction: dict, summary: dict, topic: dict) -> dict:
    """Checks with no model involved. Each returns pass or a plain-language problem."""
    source = normalise(f"{paper.title} {paper.abstract}")
    source_numbers = set(numbers_in(source))
    synonyms = topic.get("synonyms", {})
    spec = {f["id"]: f for f in topic.get("fields", [])}
    fields = {}
    for fid, item in extraction["fields"].items():
        value, quote = item["value"], item["quote"]
        if not value:
            continue
        problems = []
        if spec.get(fid, {}).get("kind") == "category":
            # A category is a judgement, not a quote: code checks it is an allowed
            # value, and the second model checks the judgement against the abstract.
            allowed = [v.casefold() for v in spec[fid]["values"]]
            if value.casefold() not in allowed:
                problems.append("Not one of the allowed values.")
            fields[fid] = {"passed": not problems, "problems": problems}
            continue
        if not quote:
            problems.append("No quote given.")
        elif not _quote_in(quote, source):
            problems.append("Quote is not in the abstract word for word.")
        else:
            missing = [a for a in _anchors(value) if not _appears(a, quote, synonyms)]
            if missing:
                problems.append("Not in the quote: " + ", ".join(missing) + ".")
            # Plain words matter too: a value must be said by its quote, not merely
            # sit next to it. A "judgement" field (study type) describes the evidence
            # in other words, so only the second model judges it.
            if spec.get(fid, {}).get("kind") != "judgement":
                words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", value)
                         if w.casefold() not in _STOPWORDS]
                absent = [w for w in words if not _appears(w, quote, synonyms)]
                if absent and len(absent) * 2 >= len(words):
                    problems.append("The quote does not say this: " + ", ".join(absent) + ".")
        stray = [n for n in numbers_in(value) if n not in source_numbers]
        if stray:
            problems.append("Number not in the abstract: " + ", ".join(stray) + ".")
        fields[fid] = {"passed": not problems, "problems": problems}

    record_text = " ".join(v["value"] for v in extraction["fields"].values() if v["value"])
    record_numbers = set(numbers_in(record_text))
    stray_summary = [n for n in numbers_in(summary["text"]) if n not in record_numbers]
    summary_check = {
        "passed": not stray_summary,
        "problems": (["Number not in the extracted record: " + ", ".join(stray_summary) + "."]
                     if stray_summary else []),
    }
    return {"fields": fields, "summary": summary_check}


def model_check(paper: Paper, extraction: dict, summary: dict, topic: dict, model: Model) -> dict:
    claims = []
    for fid, item in extraction["fields"].items():
        if item["value"]:
            claims.append({"kind": "field", "id": fid, "text": f"{fid}: {item['value']}"})
    for i, sent in enumerate(_split_summary(summary["text"]), start=1):
        claims.append({"kind": "summary", "id": f"summary {i}", "text": sent})

    listing = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(claims, start=1))
    user = (f"Topic: {topic['question']}\n\nAbstract, sentence by sentence:\n{numbered(paper)}\n\n"
            f"Claims:\n{listing}")
    raw = model.complete_json(CHECK_SYSTEM, user)
    by_claim = {as_index(v.get("claim")): v for v in raw.get("verdicts", []) if isinstance(v, dict)}
    results = []
    for i, c in enumerate(claims, start=1):
        v = by_claim.get(i, {})
        verdict = str(v.get("verdict", "beyond")).lower()
        if verdict not in ("supported", "contradicted", "beyond"):
            verdict = "beyond"
        evidence = sentence_text(paper, v.get("sentence"))
        # "Supported" must point at a real sentence, or it does not count.
        if verdict == "supported" and evidence is None:
            verdict = "beyond"
        results.append({**c, "verdict": verdict, "evidence": evidence})
    omitted = [normalise(o) for o in raw.get("omitted", []) if isinstance(o, str) and o.strip()]
    return {"claims": results, "omitted": omitted, "model": model.name}


def _split_summary(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


# ---------------------------------------------------------------- route --

VERDICT_REASON = {
    "contradicted": "the second model says the abstract states something different.",
    "beyond": "the second model could not find this in the abstract.",
}


def label(field_id: str) -> str:
    return field_id.replace("_", " ").capitalize()


def route(screening: dict, checks: dict | None, verdicts: dict | None,
          extraction: dict | None, topic: dict) -> dict:
    if screening["decision"] == "exclude":
        return {"outcome": "excluded", "reasons": [screening["reason"]], "problems": 0}
    if screening["decision"] == "review":
        return {"outcome": "review", "reasons": [screening["reason"]], "problems": 1}

    reasons, problems = [], 0
    for r in screening["criteria"]:
        if r["required"] and r["answer"] == "unclear":
            reasons.append(f"Screening could not tell from the abstract whether it meets “{label(r['id'])}”.")
    for fid, c in checks["fields"].items():
        for p in c["problems"]:
            reasons.append(f"{label(fid)}: {p}")
            problems += 1
    for p in checks["summary"]["problems"]:
        reasons.append(f"Summary: {p}")
        problems += 1
    for c in verdicts["claims"]:
        if c["verdict"] != "supported":
            name = c["id"].replace("summary", "Summary sentence") if c["kind"] == "summary" else label(c["id"])
            reasons.append(f"{name}: {VERDICT_REASON[c['verdict']]}")
            problems += 1
    if verdicts["omitted"]:
        n = len(verdicts["omitted"])
        reasons.append(f"The second model found {n} relevant finding{'s' if n > 1 else ''} the record leaves out.")
        problems += 1

    rule = topic.get("high_impact")
    if rule:
        value = (extraction["fields"].get(rule["field"], {}).get("value") or "").casefold()
        if any(v.casefold() in value for v in rule["values"]):
            reasons.append(f"Sent to a reviewer by policy: {label(rule['field']).lower()} is “{value}”.")

    return {"outcome": "review" if reasons else "accepted", "reasons": reasons, "problems": problems}
