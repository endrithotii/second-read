"""Ground truth from CIViC, the open (CC0) knowledge base of clinical
interpretations of cancer variants, curated by experts.

For this topic we keep every CIViC evidence item, accepted or still submitted
(not rejected), that links an EGFR tyrosine kinase inhibitor to a molecular
profile in lung cancer, or any molecular profile to osimertinib. Each item
carries the PubMed ID it was curated from, the paper's publication date and
the date a curator submitted it, which is what the time-travel test needs.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

API = "https://civicdb.org/api/graphql"
UA = "second-read/0.2 (+https://github.com/endrithotii/second-read)"

# EGFR tyrosine kinase inhibitors, first to fourth generation, and amivantamab.
EGFR_TKIS = ["Osimertinib", "Gefitinib", "Erlotinib", "Afatinib", "Dacomitinib", "Icotinib", "Lazertinib",
             "Aumolertinib", "Almonertinib", "Furmonertinib", "Befotertinib", "Rociletinib", "Olmutinib",
             "Nazartinib", "Mobocertinib", "Poziotinib", "Sunvozertinib", "Amivantamab"]
LUNG = ("lung", "nsclc")

QUERY = """query($therapy: String!, $after: String) {
  evidenceItems(therapyName: $therapy, status: NON_REJECTED, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id status evidenceType evidenceDirection evidenceLevel significance evidenceRating variantOrigin
      therapyInteractionType description
      disease { name doid }
      therapies { name ncitId }
      submissionEvent { createdAt }
      acceptanceEvent { createdAt }
      source { citationId sourceType publicationDate }
      molecularProfile { name variants { name ... on GeneVariant { feature { name } hgvsDescriptions } } }
    }
  }
}"""


def _post(query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API, data=body, headers={"content-type": "application/json", "user-agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.load(r)
            if "errors" in out:
                raise RuntimeError(out["errors"])
            return out["data"]
        except (OSError, RuntimeError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def _flatten(n: dict) -> dict:
    variants = []
    for v in n["molecularProfile"]["variants"]:
        variants.append({"gene": (v.get("feature") or {}).get("name"), "name": v["name"],
                         "hgvs": v.get("hgvsDescriptions") or []})
    return {
        "eid": n["id"], "status": n["status"].lower(),
        "profile": n["molecularProfile"]["name"], "variants": variants,
        "disease": (n.get("disease") or {}).get("name"), "doid": (n.get("disease") or {}).get("doid"),
        "therapies": [t["name"] for t in n["therapies"]], "interaction": n.get("therapyInteractionType"),
        "type": n["evidenceType"], "direction": n["evidenceDirection"], "level": n["evidenceLevel"],
        "significance": n["significance"], "rating": n.get("evidenceRating"), "origin": n.get("variantOrigin"),
        "statement": n.get("description") or "",
        "pmid": n["source"]["citationId"] if n["source"]["sourceType"] == "PUBMED" else None,
        "source_type": n["source"]["sourceType"], "published": n["source"].get("publicationDate"),
        "submitted": (n.get("submissionEvent") or {}).get("createdAt"),
        "accepted": (n.get("acceptanceEvent") or {}).get("createdAt"),
    }


def in_scope(item: dict) -> bool:
    """EGFR TKI evidence in lung cancer, or any osimertinib evidence."""
    lung = any(k in (item["disease"] or "").lower() for k in LUNG)
    egfr = any(v["gene"] == "EGFR" for v in item["variants"])
    return "Osimertinib" in item["therapies"] or (egfr and lung)


def fetch(out: Path) -> list[dict]:
    seen: dict[int, dict] = {}
    for therapy in EGFR_TKIS:
        after = None
        while True:
            page = _post(QUERY, {"therapy": therapy, "after": after})["evidenceItems"]
            for n in page["nodes"]:
                item = _flatten(n)
                if in_scope(item):
                    seen[item["eid"]] = item
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
    items = sorted(seen.values(), key=lambda x: x["eid"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fetched": time.strftime("%Y-%m-%d"), "items": items}, indent=1), encoding="utf-8")
    return items


def split(pmid: str, held_out_share: float = 0.7) -> str:
    """Fixed, reproducible split by paper: 'dev' papers may be looked at while
    building the prompts; 'held_out' papers are only used to report numbers."""
    h = int(hashlib.sha256(f"second-read-split:{pmid}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "held_out" if h < held_out_share else "dev"


def osimertinib_resistance(item: dict) -> bool:
    """The narrow topic: osimertinib, resistance."""
    return "Osimertinib" in item["therapies"] and item["significance"] in ("RESISTANCE", "REDUCED_SENSITIVITY")
