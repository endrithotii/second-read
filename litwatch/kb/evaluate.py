"""Every number in the report, computed from cached runs and CIViC.

Nothing here calls a model. Run it as often as you like.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import random
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from . import civic, compare, screen
from .__main__ import CACHE, CUTOFF, GT, N_NEGATIVE_SAMPLE, POOL, ground_truth, paper_sets
from .normalise import Lookups
from .sources import load_all
from .stats import bootstrap, kappa, wilson, wss_at

OUT = Path("site/kb")
FIELDS = ["drugs", "disease", "evidence_type", "direction", "significance", "level"]


def _latest(kind: str) -> dict[str, dict]:
    """Cached results of the configured models and prompt versions only, so
    runs made with older prompts never mix into the numbers."""
    from .__main__ import current_keys
    folder = CACHE / current_keys()[kind]
    return {Path(f).stem: json.loads(Path(f).read_text(encoding="utf-8")) for f in glob.glob(str(folder / "*.json"))}


def ci(t) -> dict:
    p, lo, hi = t
    return {"value": p, "low": lo, "high": hi}


def screening(items, pool, sets) -> dict:
    res = _latest("screen")
    positives = set(sets["held_out"]) & set(pool)
    negatives_in_pool = len(set(pool) - {i["pmid"] for i in items})
    sampled_neg = [p for p in screen.sample(pool, positives, N_NEGATIVE_SAMPLE) if p not in positives]
    w_neg = negatives_in_pool / max(1, len(sampled_neg))
    rows = []
    for p in sorted(positives) + sampled_neg:
        r = res.get(p)
        if not r or r.get("missing"):
            continue
        rows.append({"pmid": p, "relevant": p in positives, "include": r["include"], "conf": r["confidence"],
                     "w": 1.0 if p in positives else w_neg})
    if not rows:
        return {"done": 0}
    tp = sum(1 for r in rows if r["relevant"] and r["include"])
    pos = sum(1 for r in rows if r["relevant"])

    def precision(rs):
        tp_ = sum(r["w"] for r in rs if r["relevant"] and r["include"])
        fp_ = sum(r["w"] for r in rs if not r["relevant"] and r["include"])
        return tp_ / (tp_ + fp_) if tp_ + fp_ else None

    def wss(rs):
        return wss_at([(r["conf"] + (1000 if r["include"] else 0), r["relevant"], r["w"]) for r in rs])
    prec = precision(rows)
    return {
        "done": len(rows), "planned": len(positives) + len(sampled_neg), "positives_screened": pos,
        "negatives_screened": len(rows) - pos, "negative_weight": w_neg, "pool": len(pool),
        "recall": ci(wilson(tp, pos)),
        "precision": {"value": prec, **dict(zip(("low", "high"), bootstrap(precision, rows)))},
        "wss95": {"value": wss(rows), **dict(zip(("low", "high"), bootstrap(wss, rows)))},
        "included_share": sum(r["w"] for r in rows if r["include"]) / sum(r["w"] for r in rows),
        "missed": [r["pmid"] for r in rows if r["relevant"] and not r["include"]],
    }


def extraction(items, sets, lk) -> dict:
    res = _latest("extract")
    held = [p for p in sets["held_out"] if p in res]
    by_paper = {p: [i for i in items if i["pmid"] == p] for p in held}
    pairs, per_source = [], Counter()
    all_entries = []
    for p in held:
        ents = res[p]["entries"]
        for e in ents:
            e["_pmid"], e["_source"] = p, res[p]["text_source"]
            e["kb_label"], e["kb_items"] = compare.label(e, items, lk, exclude_pmid=p)
        all_entries += ents
        for e, it in compare.match(ents, by_paper[p]):
            pairs.append((e, it, res[p]["text_source"]))
    curated = [x for x in pairs if x[1] is not None]
    found = [x for x in curated if x[0] is not None]
    matched_entries = [x for x in pairs if x[0] is not None and x[1] is not None]

    def recall_by(src):
        c = [x for x in curated if x[2].startswith(src)]
        return ci(wilson(sum(1 for x in c if x[0] is not None), len(c))) | {"n": len(c)}

    field_acc = {}
    for f in FIELDS:
        ok = [compare.field_agreement(e, it, lk)[f] for e, it, _ in matched_entries]
        field_acc[f] = ci(wilson(sum(ok), len(ok))) | {"n": len(ok)}
    # Writer-checker agreement (Cohen's kappa) on every entry the checker read.
    checked = [e for e in all_entries if e.get("checker")]
    kap = {}
    for f in ("evidence_type", "direction", "significance"):
        a = [e.get(f) for e in checked]
        b = [(e["checker"] or {}).get(f) for e in checked]
        rows = list(zip(a, b))
        lo, hi = bootstrap(lambda rs: kappa([x for x, _ in rs], [y for _, y in rs]), rows)
        kap[f] = {"value": kappa(a, b), "low": lo, "high": hi, "n": len(rows)}
    from .entries import LEVEL_BY_DESIGN
    a = [e.get("level") for e in checked]
    b = [LEVEL_BY_DESIGN.get((e["checker"] or {}).get("design")) for e in checked]
    lo, hi = bootstrap(lambda rs: kappa([x for x, _ in rs], [y for _, y in rs]), list(zip(a, b)))
    kap["level"] = {"value": kappa(a, b), "low": lo, "high": hi, "n": len(checked)}
    # Routing.
    accepted = [e for e in all_entries if e["route"] == "accepted"]
    acc_matched = [(e, it) for e, it, _ in matched_entries if e["route"] == "accepted"]
    acc_wrong = [(e, it) for e, it in acc_matched if not all(compare.field_agreement(e, it, lk).values())]
    reasons = Counter()
    for e in all_entries:
        for r in e["reasons"]:
            reasons[r.split(":")[0].split("'")[0].strip()] += 1
    entry_matched = {id(e) for e, it, _ in matched_entries}
    return {
        "papers": len(held), "papers_planned": len(sets["held_out"]),
        "full_text_papers": sum(1 for p in held if res[p]["text_source"].startswith("full")),
        "entries": len(all_entries), "curated_items": len(curated),
        "item_recall": ci(wilson(len(found), len(curated))),
        "item_recall_full_text": recall_by("full"), "item_recall_abstract_only": recall_by("abstract"),
        "entry_precision_vs_civic": ci(wilson(len(entry_matched), len(all_entries))),
        "field_accuracy": field_acc, "kappa": kap,
        "to_person": ci(wilson(len(all_entries) - len(accepted), len(all_entries))),
        "accepted": len(accepted), "accepted_matched": len(acc_matched),
        "accepted_error_rate": ci(wilson(len(acc_wrong), len(acc_matched))),
        "accepted_errors": [{"pmid": e["_pmid"], "entry": e["id"], "civic": it["eid"],
                             "wrong": [f for f, ok in compare.field_agreement(e, it, lk).items() if not ok]}
                            for e, it in acc_wrong],
        "accepted_not_in_civic": sum(1 for e in accepted if id(e) not in entry_matched),
        "reasons": reasons.most_common(12),
        "kb_labels": Counter(e["kb_label"] for e in all_entries),
        "missed_items": [{"pmid": it["pmid"], "eid": it["eid"], "profile": it["profile"], "therapies": it["therapies"],
                          "source": s} for e, it, s in curated if e is None][:200],
        "_entries": all_entries,
    }


def osimertinib_subset(items, ext) -> dict:
    osi = [i for i in items if civic.osimertinib_resistance(i) and civic.split(i["pmid"]) == "held_out"]
    res = _latest("extract")
    found = 0
    done = 0
    for it in osi:
        if it["pmid"] not in res:
            continue
        done += 1
        if any(e is not None for e, x in compare.match(res[it["pmid"]]["entries"], [it]) if x is it):
            found += 1
    return {"items": len(osi), "evaluated": done, "recall": ci(wilson(found, done))}


def time_travel(items, sets, lk) -> dict:
    res = _latest("extract")
    later = [i for i in items if i["pmid"] and (i["submitted"] or "") >= CUTOFF]
    srcs = load_all(sorted({i["pmid"] for i in later}), CACHE, log=lambda *a: None)
    kb = compare.kb_as_of(items, CUTOFF)
    rows = []
    for it in later:
        s = srcs.get(it["pmid"])
        indexed = (s.indexed if s else "") or ""
        before = bool(indexed) and indexed < CUTOFF
        r = res.get(it["pmid"])
        flagged, label, route = False, None, None
        if r:
            for e, x in compare.match(r["entries"], [it]):
                if x is it and e is not None:
                    flagged, route = True, e["route"]
                    label = compare.label(e, kb, lk, exclude_pmid=it["pmid"])[0]
        lead = None
        if flagged and before:
            lead = (datetime.fromisoformat(it["submitted"].replace("Z", "+00:00")).date() - date.fromisoformat(indexed)).days
        rows.append({"eid": it["eid"], "pmid": it["pmid"], "profile": it["profile"], "therapies": it["therapies"],
                     "significance": it["significance"], "paper_indexed": indexed, "civic_submitted": it["submitted"][:10],
                     "paper_before_cutoff": before, "processed": bool(r), "flagged": flagged, "route": route,
                     "label_vs_kb_at_cutoff": label, "lead_days": lead})
    eligible = [r for r in rows if r["paper_before_cutoff"] and r["processed"]]
    leads = sorted(r["lead_days"] for r in eligible if r["lead_days"] is not None)
    return {"cutoff": CUTOFF, "items_after_cutoff": len(rows), "eligible": len(eligible),
            "flagged": ci(wilson(sum(r["flagged"] for r in eligible), len(eligible))),
            "median_lead_days": leads[len(leads) // 2] if leads else None, "rows": rows}


def pubtator_comparison(items, sets, ext) -> dict:
    from .pubtator import variants as pt_variants
    held = [p for p in sets["held_out"] if any(e["_pmid"] == p for e in ext["_entries"]) or p in _latest("extract")]
    pt = pt_variants(held, CACHE / "pubtator")
    sys_vars: dict[str, set] = {p: set() for p in held}
    for e in ext["_entries"]:
        sys_vars.setdefault(e["_pmid"], set()).update(compare.vkey(v.get("gene"), v.get("name")) for v in e["variants"])
    rows = []
    for it in items:
        if it["pmid"] not in held:
            continue
        for v in it["variants"]:
            k = compare.vkey(v["gene"], v["name"])
            kind = "category" if v["name"] in ("Exon 19 Deletion", "Exon 20 Insertion", "Amplification", "Mutation",
                                                 "Overexpression", "Expression", "Fusion", "Wildtype", "Loss") else "specific"
            rows.append({"pmid": it["pmid"], "key": k, "kind": kind,
                         "system": k in sys_vars.get(it["pmid"], set()), "pubtator": k in pt.get(it["pmid"], set())})
    out = {}
    for kind in ("specific", "category"):
        r = [x for x in rows if x["kind"] == kind]
        out[kind] = {"n": len(r), "system": ci(wilson(sum(x["system"] for x in r), len(r))),
                     "pubtator": ci(wilson(sum(x["pubtator"] for x in r), len(r))),
                     "only_system": sum(x["system"] and not x["pubtator"] for x in r),
                     "only_pubtator": sum(x["pubtator"] and not x["system"] for x in r)}
    curated = {(it["pmid"], compare.vkey(v["gene"], v["name"])) for it in items for v in it["variants"]}
    pt_listed = sum(len(v) for v in pt.values())
    sys_listed = sum(len(v) for v in sys_vars.values())
    out["listed_per_paper"] = {"pubtator": pt_listed / max(1, len(held)), "system": sys_listed / max(1, len(held))}
    out["share_listed_that_curators_recorded"] = {
        "pubtator": sum(1 for p, s in pt.items() for k in s if (p, k) in curated) / max(1, pt_listed),
        "system": sum(1 for p, s in sys_vars.items() for k in s if (p, k) in curated) / max(1, sys_listed)}
    return out


def prisma(items, pool, sets, scr, ext) -> dict:
    res = _latest("extract")
    held = [p for p in sets["held_out"] if p in res]
    srcs = load_all(held, CACHE, log=lambda *a: None)
    return {
        "identified_by_search": len(pool), "civic_papers": len({i["pmid"] for i in items}),
        "civic_papers_found_by_search": len({i["pmid"] for i in items} & set(pool)),
        "screened_sample": scr.get("done", 0),
        "extracted_papers": len(held),
        "full_text": sum(1 for p in held if srcs[p].text_source.startswith("full")),
        "abstract_only": sum(1 for p in held if not srcs[p].text_source.startswith("full")),
        "abstract_only_reasons": Counter(srcs[p].why_abstract_only for p in held if srcs[p].why_abstract_only),
        "retracted": sum(1 for p in held if srcs[p].retracted),
        "with_corrections": sum(1 for p in held if srcs[p].corrections),
        "entries": ext["entries"], "accepted": ext["accepted"], "to_person": ext["entries"] - ext["accepted"],
    }


ERROR_CATEGORIES = [
    "correct",
    "wrong variant or molecular profile",
    "wrong drug",
    "wrong disease",
    "wrong significance or direction",
    "wrong evidence level",
    "the quote does not support the entry",
    "out of scope (not EGFR-TKI evidence in lung cancer)",
    "duplicate of another entry",
]


def review_sample(entries: list[dict], srcs, n: int = 30, seed: int = 26102026) -> list[dict]:
    accepted = sorted((e for e in entries if e["route"] == "accepted"), key=lambda e: e["id"])
    pick = random.Random(seed).sample(accepted, min(n, len(accepted)))
    out = []
    for k, e in enumerate(pick, start=1):
        out.append({"item": k, "pmid": e["_pmid"], "paper": srcs[e["_pmid"]].title if e["_pmid"] in srcs else "",
                    "profile": " AND ".join(f"{v['gene']} {v['name']}" for v in e["variants"]),
                    "hgvs": "; ".join(v["hgvs"] or "" for v in e["variants"]),
                    "drugs": ", ".join(f"{d['name']} ({d.get('chembl_id') or 'no ChEMBL id'})" for d in e["drugs"]),
                    "disease": f"{e['disease_written']} (OncoTree {e['oncotree']})",
                    "evidence_type": e["evidence_type"], "direction": e["direction"], "significance": e["significance"],
                    "level": e["level"], "design": e["design"], "n_patients": e["n_patients"],
                    "source": e["_source"], "units": ", ".join(e["units"]), "source_text": e["cited_text"],
                    "error_category": "", "comment": ""})
    return out


def run() -> None:
    items, pool = ground_truth(), json.loads(POOL.read_text())
    sets = paper_sets(items, pool)
    lk = Lookups(CACHE)
    pmids = {i["pmid"] for i in items if i["pmid"]}
    scr = screening(items, pool, sets)
    ext = extraction(items, sets, lk)
    srcs = load_all(sorted({e["_pmid"] for e in ext["_entries"]}), CACHE, log=lambda *a: None)
    metrics = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "ground_truth": {"items": len(items), "papers": len(pmids),
                         "status": Counter(i["status"] for i in items),
                         "held_out_papers": len(sets["held_out"]), "dev_papers": len(sets["dev"]),
                         "osimertinib_resistance_items": sum(civic.osimertinib_resistance(i) for i in items)},
        "search": {"query": screen.QUERY, "pool": len(pool),
                   "recall_all": ci(wilson(len(pmids & set(pool)), len(pmids))),
                   "recall_held_out": ci(wilson(len(set(sets["held_out"]) & set(pool)), len(sets["held_out"])))},
        "screening": scr,
        "extraction": {k: v for k, v in ext.items() if not k.startswith("_")},
        "osimertinib_resistance": osimertinib_subset(items, ext),
        "time_travel": time_travel(items, sets, lk),
        "pubtator": pubtator_comparison(items, sets, ext),
        "prisma": prisma(items, pool, sets, scr, ext),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=1, default=list), encoding="utf-8")
    sample = review_sample(ext["_entries"], srcs)
    with open(OUT / "expert_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(sample[0].keys()) if sample else ["item"])
        w.writeheader()
        w.writerows(sample)
    (OUT / "expert_review.json").write_text(json.dumps({"error_categories": ERROR_CATEGORIES, "items": sample},
                                                       indent=1, ensure_ascii=False), encoding="utf-8")
    from .report import page
    (OUT / "index.html").write_text(page(metrics, sample, ERROR_CATEGORIES), encoding="utf-8")
    print(OUT / "index.html")
