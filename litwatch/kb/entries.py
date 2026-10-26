"""From a paper's full text to checked evidence entries.

1. Select (code): pick the sentences and table rows that mention a gene,
   a variant, an EGFR inhibitor or a response or resistance word. Every
   abstract sentence is kept. The model sees only these, each with its id.
2. Write (model A): one entry per molecular profile, drug and finding,
   in CIViC's vocabulary, each citing the ids it relies on and quoting a span.
3. Check (code): the cited ids exist; the quote is in a cited unit; the
   variant and the drug are named in the cited units; the patient count is
   a number in them. Names are normalised (HGNC, VariantValidator, ChEMBL,
   OncoTree). The evidence level is derived from the study design by a rule,
   not chosen by the model.
4. Re-rate (model B, a different family): reads the same cited units, is not
   shown model A's labels, and assigns type, direction, significance and
   study design itself, and says whether the units support the association.
5. Route (code): accepted only if every code check passed, every name was
   normalised, model B says the units support it, and both models agree on
   type, direction, significance and level. Anything else goes to a person,
   with the reasons.
"""

from __future__ import annotations

import re

from ..textutil import normalise, numbers_in, short_hash
from .normalise import Lookups
from .sources import Source

TKIS = ["osimertinib", "azd9291", "tagrisso", "gefitinib", "erlotinib", "afatinib", "dacomitinib", "icotinib",
        "lazertinib", "aumolertinib", "almonertinib", "furmonertinib", "alflutinib", "befotertinib", "rociletinib",
        "olmutinib", "nazartinib", "mobocertinib", "poziotinib", "sunvozertinib", "amivantamab", "savolitinib",
        "tepotinib", "capmatinib", "crizotinib", "chemotherapy", "egfr-tki", "egfr tki", "tki"]
GENES = r"\b(EGFR|MET|ERBB2|HER2|BRAF|KRAS|NRAS|PIK3CA|PTEN|RB1|TP53|ALK|RET|ROS1|FGFR\d?|CCND1|CDK4|CDK6|MYC|AXL|NTRK\d?|HER3|ERBB3|IGF1R|AKT\d?|MAPK1|NF1|STK11|KEAP1|SMARCA4|YAP1)\b"
VARIANT = r"\b[A-Z]\d{2,4}[A-Z*]\b|\b(?:Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|Trp|Tyr|Val)\d{2,4}|\bexon\s*(?:18|19|20|21)\b|amplif|fusion|deletion|insertion"
OUTCOME = r"resist|sensitiv|respon|progress|efficacy|survival|PFS|ORR|benefit|refractor|acquired|mechanism|IC50|inhibit"

TYPES = ["PREDICTIVE", "PROGNOSTIC", "DIAGNOSTIC", "PREDISPOSING", "ONCOGENIC", "FUNCTIONAL"]
DIRECTIONS = ["SUPPORTS", "DOES_NOT_SUPPORT"]
SIGNIFICANCE = ["SENSITIVITYRESPONSE", "RESISTANCE", "REDUCED_SENSITIVITY", "ADVERSE_RESPONSE", "NA"]
DESIGNS = ["guideline or regulatory approval", "randomised trial", "single-arm trial", "cohort or case series",
           "case report", "animal model", "cell line or biochemical", "review or inference"]
# CIViC evidence level, decided by code from the study design (CIViC's own definitions):
# A validated association, B clinical evidence, C case study, D preclinical, E inferential.
LEVEL_BY_DESIGN = {"guideline or regulatory approval": "A", "randomised trial": "B", "single-arm trial": "B",
                   "cohort or case series": "B", "case report": "C", "animal model": "D",
                   "cell line or biochemical": "D", "review or inference": "E"}

DEFINITIONS = """Definitions (CIViC):
  PREDICTIVE: the alteration changes response or resistance to a therapy, in patients or in preclinical models
              (cell lines treated with the drug count as PREDICTIVE).
  PROGNOSTIC: the alteration is linked to outcome regardless of a specific therapy.
  FUNCTIONAL: the alteration changes the protein's function (e.g. kinase activity), with no drug response studied.
  ONCOGENIC: the alteration drives cancer development. DIAGNOSTIC / PREDISPOSING as in CIViC.
  SUPPORTS / DOES_NOT_SUPPORT: whether the finding supports the stated significance.
  REDUCED_SENSITIVITY: response is lower than for the reference alteration but not absent."""

WRITER = f"""You curate clinical evidence about EGFR tyrosine kinase inhibitors in EGFR-mutant lung cancer,
the way CIViC curators do. You receive numbered units from one paper: sentences (A = abstract, S = body)
and table rows (T or ST, with cell ids).

Write one entry for each distinct finding that links a molecular profile to response or resistance to a drug.
Each entry:
  "variants": list of {{"gene": "<gene symbol>", "variant": "<as written, e.g. C797S, exon 19 deletion, amplification>"}}
              (several only when the finding needs them together, e.g. T790M with C797S in cis)
  "disease": as written in the paper
  "drugs": list of drug names as written (one per drug; combinations as separate names)
  "evidence_type": one of {TYPES}
  "direction": one of {DIRECTIONS} (does the paper's finding support or argue against the association)
  "significance": one of {SIGNIFICANCE}
  "design": one of {DESIGNS}
  "n_patients": number of patients the finding is based on, digits only, or null
  "units": ids of the units the finding rests on (for tables use the cell or row id, e.g. T2.r3.c4 or T2.r3)
  "quote": exact words copied from one of those units that state the finding
{DEFINITIONS}
Rules:
- Use only the units given. Do not add drugs, variants or numbers that are not in them.
- The quote must be one run of consecutive words from one unit, copied exactly.
- One entry per variant and drug, unless the finding is about variants occurring together (e.g. in cis):
  "cells with L858R, G719S or exon 19 deletion responded to erlotinib" is three entries.
- Name specific drugs; write a drug class ("EGFR TKIs") only when the paper names no drug.
- If the paper has no such finding, return an empty list.
Reply with JSON only: {{"entries": [...]}}"""

CHECKER = f"""You review proposed evidence entries about cancer genomics. You did not write them.
For each entry you see the molecular profile, disease and drugs it names, and the units of the paper it cites.
You are not shown the writer's labels: assign them yourself, from the cited units only.
{DEFINITIONS}
For each entry give:
  "entry": its number
  "supported": "yes" if the units state that this profile is associated with response or resistance to these
               drugs in this disease; "partly" if they state part of it; "no" otherwise
  "evidence_type": one of {TYPES}
  "direction": one of {DIRECTIONS}
  "significance": one of {SIGNIFICANCE}
  "design": one of {DESIGNS}
  "reason": one short sentence
Reply with JSON only: {{"reviews": [{{"entry": 1, "supported": "...", "evidence_type": "...", "direction": "...", "significance": "...", "design": "...", "reason": "..."}}]}}"""

PROMPT_VERSIONS = {"kb_writer": short_hash(WRITER), "kb_checker": short_hash(CHECKER)}


# ------------------------------------------------------------ selection --

def _row_units(src: Source) -> dict[str, str]:
    rows = {}
    for t in src.tables:
        header = t["rows"][0] if t["rows"] else []
        for r, row in enumerate(t["rows"], start=1):
            if r == 1 or not any(row):
                continue
            cells = []
            for c, cell in enumerate(row, start=1):
                if cell:
                    head = header[c - 1] if c - 1 < len(header) and header[c - 1] else f"col {c}"
                    cells.append(f"[{t['id']}.r{r}.c{c}] {head}: {cell}")
            rows[f"{t['id']}.r{r}"] = f"({t['label'] or t['id']}: {t['caption'][:120]}) " + "; ".join(cells)
    return rows


def select_units(src: Source, max_body: int = 40, max_rows: int = 15) -> dict[str, str]:
    units = {f"A{i}": s for i, s in enumerate(src.abstract, start=1)}
    scored = []
    for b in src.body:
        t = b["text"]
        score = (2 * bool(re.search(GENES, t)) + 2 * bool(re.search(VARIANT, t)) +
                 2 * any(d in t.lower() for d in TKIS) + bool(re.search(OUTCOME, t, re.I)))
        if re.match(r"(?i)(methods?|materials|reference|acknowledg|funding|conflict)", b["section"] or ""):
            score -= 2
        if score >= 4:
            scored.append((score, b["id"], t))
    keep = sorted(scored, key=lambda x: -x[0])[:max_body]
    for _, i, t in sorted(keep, key=lambda x: int(x[1][1:])):
        units[i] = t
    rows = [(k, v) for k, v in _row_units(src).items()
            if re.search(GENES, v) or re.search(VARIANT, v) or any(d in v.lower() for d in TKIS)]
    for k, v in rows[:max_rows]:
        units[k] = v
    return units


def render(units: dict[str, str]) -> str:
    return "\n".join(f"[{k}] {v}" for k, v in units.items())


# --------------------------------------------------------------- checks --

_FIGREF = re.compile(r"\s*[\(\[](?:[^()\[\]]*?\b(?:fig|figure|figs|table|tables|supplementary|additional file|data not shown|unpublished)\b[^()\[\]]*)[\)\]]", re.I)


def _norm(s: str) -> str:
    """For comparing a quote with its source: case, spacing, figure and table
    pointers in brackets, empty brackets left by removed citations, and the
    final punctuation do not count. Everything else must match exactly."""
    s = normalise(s).lower().replace("–", "-")
    s = _FIGREF.sub("", s)
    s = re.sub(r"\s*[\(\[]\s*[,;–-]?\s*[\)\]]", "", s)
    return re.sub(r"\s+", " ", s).strip(" .;:,")


def _cited_text(src_units: dict[str, str], ids: list[str], selected: dict[str, str]) -> tuple[list[str], str]:
    """Resolve cited ids (a cell, a row, a sentence) to text. Returns (valid ids, joined text)."""
    ok, texts = [], []
    for i in ids:
        i = str(i).strip().strip("[]")
        if i in src_units:
            ok.append(i)
            texts.append(src_units[i])
        elif i in selected:          # a whole table row
            ok.append(i)
            texts.append(selected[i])
    return ok, " ".join(texts)


def _variant_named(v: dict, text: str) -> bool:
    name = (v.get("variant") or "")
    t = _norm(text)
    m = re.search(r"(\d{2,4})", name)
    if m and re.match(r"^[A-Za-z]{1,3}\d", name.replace("p.", "")):
        return m.group(1) in t
    keywords = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2 and w not in ("the", "and", "mutation")]
    return any(k[:5] in t for k in keywords) if keywords else False


def check_entry(e: dict, src: Source, selected: dict[str, str], lk: Lookups) -> dict:
    problems: list[str] = []
    all_units = src.units()
    ids, text = _cited_text(all_units, e.get("units") or [], selected)
    if not ids:
        problems.append("cites no unit that exists in the paper")
    quote = _norm(e.get("quote") or "")
    if len(quote) < 12:
        problems.append("quote missing or too short")
    elif quote not in _norm(text):
        problems.append("quote is not in the cited units")
    variants = [v for v in (e.get("variants") or []) if isinstance(v, dict)]
    if not variants:
        problems.append("no variant given")
    for v in variants:
        if not _variant_named(v, text):
            problems.append(f"variant '{v.get('variant')}' is not named in the cited units")
    drugs = [d for d in (e.get("drugs") or []) if isinstance(d, str) and d.strip()]
    for d in drugs:
        if d.lower()[:6] not in _norm(text) and not any(a in _norm(text) for a in _drug_aliases(d)):
            problems.append(f"drug '{d}' is not named in the cited units")
    n = e.get("n_patients")
    if n not in (None, "", "null"):
        if str(n).strip() not in numbers_in(text):
            problems.append(f"patient count {n} is not a number in the cited units")
    # Controlled vocabulary.
    for f, allowed in (("evidence_type", TYPES), ("direction", DIRECTIONS), ("significance", SIGNIFICANCE), ("design", DESIGNS)):
        if e.get(f) not in allowed:
            problems.append(f"{f} '{e.get(f)}' is not an allowed value")
    # Normalisation.
    norm_vars = [lk.variant(v.get("gene"), v.get("variant") or "") for v in variants]
    for nv in norm_vars:
        if not nv.get("gene"):
            problems.append("gene not found in HGNC")
        if nv["validated"] is False:
            problems.append(f"variant {nv['name']} could not be validated: {nv['note'][:120]}")
    norm_drugs = [lk.drug(d) for d in drugs]
    for d, nd in zip(drugs, norm_drugs):
        if nd is None and d.lower() not in ("chemotherapy", "egfr-tki", "egfr tki", "tki"):
            problems.append(f"drug '{d}' not found in ChEMBL")
    disease = lk.disease(e.get("disease") or "")
    if not disease:
        problems.append(f"disease '{e.get('disease')}' not mapped to OncoTree")
    level = LEVEL_BY_DESIGN.get(e.get("design"))
    return {
        "variants": norm_vars, "drugs": [nd or {"chembl_id": None, "name": d.upper()} for d, nd in zip(drugs, norm_drugs)],
        "disease_written": e.get("disease"), "oncotree": disease,
        "evidence_type": e.get("evidence_type"), "direction": e.get("direction"), "significance": e.get("significance"),
        "design": e.get("design"), "level": level, "n_patients": n,
        "units": ids, "quote": e.get("quote"), "cited_text": text[:3000], "code_problems": problems,
    }


_ALIASES = {"osimertinib": ["azd9291", "tagrisso"], "aumolertinib": ["almonertinib"], "furmonertinib": ["alflutinib"]}


def _drug_aliases(d: str) -> list[str]:
    d = d.lower()
    out = list(_ALIASES.get(d, []))
    for k, v in _ALIASES.items():
        if d in v:
            out.append(k)
    return out


# -------------------------------------------------------------- pipeline --

def profile_text(entry: dict) -> str:
    return " AND ".join(f"{v['gene']} {v['name']}" for v in entry["variants"])


def process(src: Source, writer, checker, lk: Lookups) -> dict:
    selected = select_units(src)
    rows_only = {k: v for k, v in selected.items() if re.match(r"^S?T\d+\.r\d+$", k)}
    raw = writer.complete_json(WRITER, f"Paper: {src.title}\n\n{render(selected)}")
    proposed = raw.get("entries") if isinstance(raw, dict) else None
    proposed = [e for e in (proposed or []) if isinstance(e, dict)]
    entries = []
    for k, e in enumerate(proposed, start=1):
        c = check_entry(e, src, rows_only, lk)
        c["id"] = f"{src.pmid}-{k}"
        c["checker"] = None
        entries.append(c)
    # One second reading per paper, covering every entry that cites real units.
    to_check = [c for c in entries if c["units"]]
    if checker is not None and to_check:
        blocks = []
        for n, c in enumerate(to_check, start=1):
            blocks.append(f"ENTRY {n}\nProfile: {profile_text(c)}\nDisease: {c['disease_written']}\n"
                          f"Drugs: {', '.join(d['name'] for d in c['drugs'])}\nUnits: {c['cited_text'][:1500]}")
        review = checker.complete_json(CHECKER, "\n\n".join(blocks))
        reviews = review.get("reviews") if isinstance(review, dict) else None
        for r in reviews or []:
            if not isinstance(r, dict):
                continue
            m = re.search(r"\d+", str(r.get("entry")))
            n = int(m.group()) if m else 0
            if 1 <= n <= len(to_check):
                to_check[n - 1]["checker"] = {k2: r.get(k2) for k2 in
                                              ("supported", "evidence_type", "direction", "significance", "design", "reason")}
    for c in entries:
        route(c)
        if src.retracted:
            c["route"], c["reasons"] = "to a person", c["reasons"] + ["the paper is retracted"]
    return {"pmid": src.pmid, "text_source": src.text_source, "why_abstract_only": src.why_abstract_only,
            "units_shown": len(selected), "units_total": len(src.units()), "entries": entries,
            "retracted": src.retracted, "corrections": src.corrections}


def route(c: dict) -> None:
    reasons = list(c["code_problems"])
    ch = c.get("checker")
    if not ch:
        reasons.append("no second reading")
    else:
        if str(ch.get("supported", "")).lower() != "yes":
            reasons.append(f"second model: supported = {ch.get('supported')} ({ch.get('reason') or ''})")
        for f in ("evidence_type", "direction", "significance"):
            if ch.get(f) != c.get(f):
                reasons.append(f"models disagree on {f}: {c.get(f)} vs {ch.get(f)}")
        if LEVEL_BY_DESIGN.get(ch.get("design")) != c.get("level"):
            reasons.append(f"models disagree on evidence level: {c.get('level')} vs {LEVEL_BY_DESIGN.get(ch.get('design'))}")
    c["route"] = "accepted" if not reasons else "to a person"
    c["reasons"] = reasons
