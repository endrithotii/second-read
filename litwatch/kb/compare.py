"""Compare entries with the knowledge base, and match them to curated items.

Label (decided by code):
  new          no knowledge-base item links this molecular profile to these drugs
  supports     an item with the same profile and drugs says the same thing
               (same significance and direction)
  contradicts  an item with the same profile and drugs says the opposite
               (resistance against sensitivity, or supports against does not support)
  refines      an item with the same profile and drugs exists, but in a different
               disease branch or at a different evidence level

The knowledge base is CIViC as it stood at a given date. When a paper is one
of CIViC's own sources, items curated from that same paper are left out, so a
paper cannot confirm itself.
"""

from __future__ import annotations

import re

from .normalise import Lookups, same_disease_branch

OPPOSITE = {"SENSITIVITYRESPONSE": {"RESISTANCE", "REDUCED_SENSITIVITY"},
            "RESISTANCE": {"SENSITIVITYRESPONSE"}, "REDUCED_SENSITIVITY": {"SENSITIVITYRESPONSE"}}


def vkey(gene: str | None, name: str | None) -> str:
    return f"{(gene or '').upper()}:{re.sub(r'[^a-z0-9]', '', (name or '').lower())}"


def item_profile(item: dict) -> frozenset:
    return frozenset(vkey(v["gene"], v["name"]) for v in item["variants"])


def entry_profile(e: dict) -> frozenset:
    return frozenset(vkey(v.get("gene"), v.get("name")) for v in e["variants"])


SALTS = r"\s+(ANHYDROUS|MESYLATE|MESILATE|HYDROCHLORIDE|DIHYDROCHLORIDE|DIMALEATE|MALEATE|TOSYLATE|SULFATE|CITRATE|MONOHYDRATE)$"


def drug_name(name: str) -> str:
    """Parent drug name: 'DACOMITINIB ANHYDROUS' and 'Dacomitinib' are the same drug."""
    return re.sub(SALTS, "", (name or "").upper().strip())


def profile_matches(e: dict, item: dict) -> bool:
    """Same molecular profile. CIViC profiles joined by OR ("EGFR L858R OR EGFR
    Exon 19 Deletion") match an entry about any one of their variants; profiles
    joined by AND need the same set of variants."""
    ep, ip = entry_profile(e), item_profile(item)
    if " OR " in (item.get("profile") or "") and " AND " not in item["profile"]:
        return len(ep) == 1 and ep <= ip
    return ep == ip


def item_drugs(item: dict) -> frozenset:
    return frozenset(drug_name(t) for t in item["therapies"])


def entry_drugs(e: dict) -> frozenset:
    return frozenset(drug_name(d.get("name") or "") for d in e["drugs"])


def kb_as_of(items: list[dict], date: str | None) -> list[dict]:
    if not date:
        return items
    return [i for i in items if (i.get("submitted") or "9999") < date]


def label(e: dict, kb: list[dict], lk: Lookups, exclude_pmid: str | None = None) -> tuple[str, list[int]]:
    drugs = entry_drugs(e)
    same = [i for i in kb if i["pmid"] != exclude_pmid and profile_matches(e, i) and (item_drugs(i) & drugs)]
    if not same:
        return "new", []
    disease = e.get("oncotree")
    agree = [i for i in same if i["significance"] == e.get("significance") and i["direction"] == e.get("direction")]
    opposite = [i for i in same if (i["significance"] in OPPOSITE.get(e.get("significance") or "", set()) and
                                    i["direction"] == e.get("direction")) or
                (i["significance"] == e.get("significance") and i["direction"] != e.get("direction"))]
    if opposite and not agree:
        return "contradicts", [i["eid"] for i in opposite]
    exact = [i for i in agree if same_disease_branch(lk.disease(i["disease"] or ""), disease) and i["level"] == e.get("level")]
    if exact:
        return "supports", [i["eid"] for i in exact]
    if agree:
        return "refines", [i["eid"] for i in agree]
    return "refines", [i["eid"] for i in same]


def match(entries: list[dict], items: list[dict]) -> list[tuple[dict | None, dict | None]]:
    """Pair system entries with CIViC items from the same paper: same molecular
    profile and at least one drug in common. Greedy, one-to-one."""
    pairs, used = [], set()
    for it in items:
        best = None
        for k, e in enumerate(entries):
            if k in used:
                continue
            if profile_matches(e, it) and (entry_drugs(e) & item_drugs(it)):
                if best is None or (e.get("significance") == it["significance"]):
                    best = k
        if best is None:
            pairs.append((None, it))
        else:
            used.add(best)
            pairs.append((entries[best], it))
    pairs += [(e, None) for k, e in enumerate(entries) if k not in used]
    return pairs


def field_agreement(e: dict, it: dict, lk: Lookups) -> dict[str, bool]:
    return {
        "variant": True,  # matched on it
        "drugs": entry_drugs(e) == item_drugs(it),
        "disease": same_disease_branch(e.get("oncotree"), lk.disease(it["disease"] or "")),
        "evidence_type": e.get("evidence_type") == it["type"],
        "direction": e.get("direction") == it["direction"],
        "significance": e.get("significance") == it["significance"],
        "level": e.get("level") == it["level"],
    }
