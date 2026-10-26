"""Screening: which papers in the search results hold evidence worth curating.

The model answers three criteria and gives a confidence; code decides.
Included = every criterion "yes" or "unclear" (nothing relevant is dropped
for lack of detail in the abstract). Papers are ranked by the model's
confidence, which is what work saved at 95% recall (WSS@95) is computed on.
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request

from ..textutil import short_hash

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# One search for the whole topic, written like a surveillance search would be.
QUERY = ('(EGFR[tiab] OR "ErbB Receptors"[mh]) AND (lung[tiab] OR NSCLC[tiab]) AND '
         '(osimertinib[tiab] OR AZD9291[tiab] OR gefitinib[tiab] OR erlotinib[tiab] OR afatinib[tiab] OR '
         'dacomitinib[tiab] OR icotinib[tiab] OR lazertinib[tiab] OR aumolertinib[tiab] OR almonertinib[tiab] OR '
         'furmonertinib[tiab] OR rociletinib[tiab] OR amivantamab[tiab] OR mobocertinib[tiab] OR '
         '"tyrosine kinase inhibitor"[tiab] OR "tyrosine kinase inhibitors"[tiab] OR TKI[tiab] OR TKIs[tiab])')

CRITERIA = [
    ("population", "Concerns EGFR-mutant lung cancer (patients, tumour samples, cell lines or animal models)."),
    ("drug", "Involves an EGFR tyrosine kinase inhibitor or amivantamab, alone or combined."),
    ("evidence", "Reports original data linking a specific molecular alteration (e.g. a mutation, amplification or "
                 "fusion) to response or resistance to that drug."),
]

SCREEN = ("You screen PubMed abstracts for curators of a cancer variant knowledge base.\n"
          "Answer each criterion with yes, no or unclear, from the title and abstract only.\n"
          + "\n".join(f'  "{k}": {t}' for k, t in CRITERIA) +
          '\nAlso give "confidence": 0 to 100, how likely the full paper holds such evidence.\n'
          'Reply with JSON only: {"population": "...", "drug": "...", "evidence": "...", "confidence": <int>}')

PROMPT_VERSION = short_hash(SCREEN)


def _esearch(params: dict) -> dict:
    url = ESEARCH + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "second-read/0.2"}), timeout=180) as r:
                return json.load(r)["esearchresult"]
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def search(query: str = QUERY, maxdate: str | None = None) -> list[str]:
    """All PMIDs for the query, one publication year at a time, because PubMed
    returns at most 9,999 ids per request."""
    end_year = int((maxdate or "2100")[:4])
    out: list[str] = []
    for year in range(1995, min(end_year, 2100) + 1):
        params = {"db": "pubmed", "term": f"({query}) AND {year}[dp]", "retmode": "json", "retmax": 9999,
                  "tool": "second-read"}
        if maxdate:
            params.update({"datetype": "edat", "mindate": "1900/01/01", "maxdate": maxdate.replace("-", "/")})
        res = _esearch(params)
        if int(res["count"]) > 9999:
            raise RuntimeError(f"{year}: more than 9,999 results, split further")
        out += res["idlist"]
        time.sleep(0.35)
        if year >= 2026 and not maxdate:
            break
    return sorted(set(out), key=int)


def decide(answer: dict) -> tuple[bool, float]:
    vals = [str(answer.get(k, "")).strip().lower() for k, _ in CRITERIA]
    include = all(v in ("yes", "unclear") for v in vals)
    try:
        conf = float(answer.get("confidence"))
    except (TypeError, ValueError):
        conf = 50.0
    return include, max(0.0, min(100.0, conf))


def sample(pool: list[str], positives: set[str], n_neg: int, seed: int = 20261026) -> list[str]:
    """Every positive in the pool, plus a fixed random sample of the rest."""
    rest = sorted(p for p in pool if p not in positives)
    rng = random.Random(seed)
    return sorted(p for p in pool if p in positives) + sorted(rng.sample(rest, min(n_neg, len(rest))))


def screen_one(src, model) -> dict:
    text = f"Title: {src.title}\nAbstract: {' '.join(src.abstract) or '(no abstract)'}"
    ans = model.complete_json(SCREEN, text)
    include, conf = decide(ans if isinstance(ans, dict) else {})
    return {"pmid": src.pmid, "answers": ans, "include": include, "confidence": conf}


def search_window(mindate: str, maxdate: str, query: str = QUERY) -> list[str]:
    """PMIDs added to PubMed (Entrez date) between two dates."""
    res = _esearch({"db": "pubmed", "term": query, "retmode": "json", "retmax": 9999, "tool": "second-read",
                    "datetype": "edat", "mindate": mindate.replace("-", "/"), "maxdate": maxdate.replace("-", "/")})
    return sorted(res["idlist"], key=int, reverse=True)
