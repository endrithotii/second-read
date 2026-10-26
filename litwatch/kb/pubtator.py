"""PubTator3 as a comparison point: NCBI's own text-mining of genes and variants.

PubTator3 tags every gene and variant mention in a paper (abstract, and full
text where PMC allows). It does not say which mention carries clinical
evidence, so the fair comparison is: of the variants curators recorded from a
paper, how many does each method find in it, and how much else does it list.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

URL = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson?pmids={ids}&full=true"


def variants(pmids: list[str], cache: Path) -> dict[str, set[str]]:
    """{pmid: {"EGFR:t790m", ...}} using the same key as compare.vkey."""
    from .compare import vkey
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, set[str]] = {}
    todo = [p for p in pmids if not (cache / f"{p}.json").exists()]
    for i in range(0, len(todo), 20):
        chunk = todo[i:i + 20]
        req = urllib.request.Request(URL.format(ids=",".join(chunk)), headers={"user-agent": "second-read/0.2"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        docs = data.get("PubTator3", data) if isinstance(data, dict) else data
        got = set()
        for doc in docs:
            pmid = str(doc.get("pmid") or doc.get("id"))
            got.add(pmid)
            (cache / f"{pmid}.json").write_text(json.dumps(doc), encoding="utf-8")
        for p in chunk:
            if p not in got:
                (cache / f"{p}.json").write_text("{}", encoding="utf-8")
        time.sleep(0.5)
    genes = {"1956": "EGFR", "4233": "MET", "2064": "ERBB2", "673": "BRAF", "3845": "KRAS", "5290": "PIK3CA"}
    for p in pmids:
        doc = json.loads((cache / f"{p}.json").read_text(encoding="utf-8"))
        found = set()
        for passage in doc.get("passages", []):
            for a in passage.get("annotations", []):
                inf = a.get("infons", {})
                if inf.get("type") not in ("Variant", "Mutation"):
                    continue
                ident = inf.get("identifier") or ""
                m = re.search(r"CorrespondingGene:(\d+)", ident)
                gene = genes.get(m.group(1), m.group(1)) if m else "?"
                name = (inf.get("name") or a.get("text") or "")
                name = re.sub(r"^[pc]\.", "", name)
                found.add(vkey(gene, name))
        out[p] = found
    return out
