"""Knowledge-base mode: evidence entries for one topic, measured against CIViC.

  python -m litwatch.kb ground-truth        CIViC items for the topic -> data/civic/ground_truth.json
  python -m litwatch.kb pool                the topic search -> data/pool.json (all years)
  python -m litwatch.kb screen  [--limit N] screen the held-out CIViC papers plus a fixed random sample of the rest
  python -m litwatch.kb extract [--set held_out|time_travel|recent] [--limit N]
  python -m litwatch.kb evaluate            metrics -> site/kb/metrics.json, page -> site/kb/index.html

Every model answer is cached per paper, model and prompt version, so runs
resume after a provider's daily limit (exit code 3) and cost nothing twice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from ..llm import QuotaExhausted, family, make_model
from . import civic, entries, screen
from .normalise import Lookups
from .sources import load_all, metadata

ROOT = Path(".")
GT = ROOT / "data/civic/ground_truth.json"
POOL = ROOT / "data/pool.json"
CACHE = ROOT / "cache/kb"
WRITER = os.environ.get("KB_WRITER", "groq:openai/gpt-oss-120b")
CHECKER = os.environ.get("KB_CHECKER", "groq:qwen/qwen3.8-27b")
SCREENER = os.environ.get("KB_SCREENER", "groq:openai/gpt-oss-20b")
CUTOFF = os.environ.get("KB_CUTOFF", "2025-10-26")   # time-travel date: twelve months before the first run
N_NEGATIVE_SAMPLE = 200


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ground_truth() -> list[dict]:
    return json.loads(GT.read_text(encoding="utf-8"))["items"]


def paper_sets(items: list[dict], pool: list[str]) -> dict[str, list[str]]:
    pmids = sorted({i["pmid"] for i in items if i["pmid"]}, key=int)
    held = [p for p in pmids if civic.split(p) == "held_out"]
    # Time travel: CIViC items submitted after the cutoff, from papers indexed before it.
    later = sorted({i["pmid"] for i in items if i["pmid"] and (i["submitted"] or "") >= CUTOFF}, key=int)
    return {"held_out": held, "dev": [p for p in pmids if p not in held], "time_travel": later}


def cache_get(kind: str, key: str) -> dict | None:
    f = CACHE / kind / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def cache_put(kind: str, key: str, value: dict) -> None:
    f = CACHE / kind / f"{key}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def stamp(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:10]


def current_keys() -> dict[str, str]:
    """Cache folder names for the configured models and prompt versions."""
    return {"screen": f"screen-{stamp(SCREENER.split(':', 1)[0] + '/' + SCREENER.split(':', 1)[1], screen.PROMPT_VERSION)}",
            "extract": f"extract-{stamp(WRITER.split(':', 1)[0] + '/' + WRITER.split(':', 1)[1], CHECKER.split(':', 1)[0] + '/' + CHECKER.split(':', 1)[1], entries.PROMPT_VERSIONS['kb_writer'], entries.PROMPT_VERSIONS['kb_checker'])}"}


def cmd_screen(limit: int | None) -> int:
    items, pool = ground_truth(), json.loads(POOL.read_text())
    sets = paper_sets(items, pool)
    positives = set(sets["held_out"])
    todo = screen.sample(pool, positives, N_NEGATIVE_SAMPLE)
    model = make_model(SCREENER)
    key = stamp(model.name, screen.PROMPT_VERSION)
    pending = [p for p in todo if not cache_get(f"screen-{key}", p)]
    log(f"screening: {len(todo)} papers in the sample, {len(pending)} not yet screened")
    srcs = metadata(pending[: limit or len(pending)]) if pending else {}
    for n, p in enumerate(pending[: limit or len(pending)], start=1):
        src = srcs.get(p)
        if src is None:
            cache_put(f"screen-{key}", p, {"pmid": p, "missing": True})
            continue
        try:
            res = screen.screen_one(src, model)
        except QuotaExhausted as e:
            log(f"stopped: {e}")
            return 3
        res.update({"model": model.name, "prompt": screen.PROMPT_VERSION, "title": src.title})
        cache_put(f"screen-{key}", p, res)
        if n % 10 == 0:
            log(f"  screened {n}/{len(pending)}")
    return 0


def cmd_extract(which: str, limit: int | None) -> int:
    items, pool = ground_truth(), json.loads(POOL.read_text())
    if family(WRITER) == family(CHECKER):
        log("writer and checker must come from different model families")
        return 2
    if which == "recent":
        try:
            pmids = recent_candidates()
        except QuotaExhausted as e:
            log(f"stopped while choosing recent papers: {e}")
            return 3
    else:
        pmids = paper_sets(items, pool)[which]
    srcs = load_all(pmids, CACHE, log=log)
    writer, checker = make_model(WRITER), make_model(CHECKER)
    key = stamp(writer.name, checker.name, entries.PROMPT_VERSIONS["kb_writer"], entries.PROMPT_VERSIONS["kb_checker"])
    lk = Lookups(CACHE)
    pending = [p for p in pmids if not cache_get(f"extract-{key}", p)]
    log(f"extract {which}: {len(pmids)} papers, {len(pending)} to do")
    for n, p in enumerate(pending[: limit or len(pending)], start=1):
        try:
            res = entries.process(srcs[p], writer, checker, lk)
        except QuotaExhausted as e:
            log(f"stopped: {e}")
            return 3
        res.update({"writer": writer.name, "checker": checker.name, "prompts": entries.PROMPT_VERSIONS,
                    "processed": date.today().isoformat()})
        cache_put(f"extract-{key}", p, res)
        log(f"  {n}/{len(pending)} {p}: {len(res['entries'])} entries, {res['text_source']}")
    return 0


def recent_candidates(n: int = 20) -> list[str]:
    """Papers added to PubMed in the last twelve months that the screener included and CIViC has not curated."""
    items = ground_truth()
    curated = {i["pmid"] for i in items}
    path = ROOT / "data/recent.json"
    if path.exists():
        return json.loads(path.read_text())["pmids"]
    since = (date.today() - timedelta(days=365)).isoformat()
    ids = screen.search_window(since, date.today().isoformat())
    ids = [p for p in ids if p not in curated]
    model = make_model(SCREENER)
    key = stamp(model.name, screen.PROMPT_VERSION)
    srcs = metadata(ids)
    chosen = []
    for p in ids:
        hit = cache_get(f"screen-{key}", p)
        if not hit and p in srcs:
            hit = screen.screen_one(srcs[p], model)
            cache_put(f"screen-{key}", p, hit)
        if hit and hit.get("include") and hit.get("confidence", 0) >= 70:
            chosen.append(p)
        if len(chosen) >= n:
            break
    path.write_text(json.dumps({"since": since, "pmids": chosen}, indent=1))
    return chosen


def cmd_predict() -> int:
    """Entries from recent papers that CIViC does not hold yet, written once to a
    dated file. The commit that adds the file is the timestamp: later CIViC
    releases show which predictions curators confirmed."""
    from . import compare
    recent = ROOT / "data/recent.json"
    if not recent.exists():
        log("no recent papers selected yet: run extract --set recent first")
        return 2
    pmids = json.loads(recent.read_text())["pmids"]
    folder = CACHE / current_keys()["extract"]
    done = {p: json.loads((folder / f"{p}.json").read_text()) for p in pmids if (folder / f"{p}.json").exists()}
    if len(done) < len(pmids):
        log(f"only {len(done)} of {len(pmids)} recent papers extracted; run extract --set recent until done")
        return 3
    items, lk = ground_truth(), Lookups(CACHE)
    out = []
    for p, r in done.items():
        for e in r["entries"]:
            lab, ids = compare.label(e, items, lk)
            if lab == "supports":
                continue
            out.append({"pmid": p, "entry": e["id"], "label": lab, "civic_items": ids, "route": e["route"],
                        "profile": " AND ".join(f"{v['gene']} {v['name']}" for v in e["variants"]),
                        "hgvs": [v["hgvs"] for v in e["variants"]], "drugs": [d["name"] for d in e["drugs"]],
                        "disease": e["oncotree"], "significance": e["significance"], "direction": e["direction"],
                        "level": e["level"], "quote": e["quote"], "units": e["units"]})
    day = date.today().isoformat()
    path = ROOT / f"data/predictions/{day}.json"
    if any((ROOT / "data/predictions").glob("*.json")):
        log("a prediction list already exists; it is never rewritten")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"made": day, "civic_snapshot": json.loads(GT.read_text())["fetched"],
                                "writer": WRITER, "checker": CHECKER, "predictions": out}, indent=1, ensure_ascii=False))
    log(f"{len(out)} predictions -> {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="litwatch.kb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ground-truth")
    sub.add_parser("pool")
    s = sub.add_parser("screen")
    s.add_argument("--limit", type=int)
    e = sub.add_parser("extract")
    e.add_argument("--set", default="held_out", choices=["held_out", "dev", "time_travel", "recent"])
    e.add_argument("--limit", type=int)
    sub.add_parser("evaluate")
    sub.add_parser("predict", help="write the timestamped list of predicted new entries from recent papers")
    a = ap.parse_args(argv)
    if a.cmd == "ground-truth":
        items = civic.fetch(GT)
        log(f"{len(items)} CIViC items")
        return 0
    if a.cmd == "pool":
        POOL.write_text(json.dumps(screen.search()))
        return 0
    if a.cmd == "screen":
        return cmd_screen(a.limit)
    if a.cmd == "extract":
        return cmd_extract(a.set, a.limit)
    if a.cmd == "predict":
        return cmd_predict()
    if a.cmd == "evaluate":
        from .evaluate import run as evaluate
        evaluate()
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
