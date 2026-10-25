"""Runs the whole pipeline for one topic and one time window, and keeps an
audit trail of everything a reviewer or a regulator could ask about."""

from __future__ import annotations

import json
import platform
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from . import __version__
from .llm import Model, QuotaExhausted
from .misses import audit_sample, capture_recapture
from .pubmed import Paper, PubMedClient
from .stages import (PROMPT_VERSIONS, code_checks, extract, model_check, route, screen,
                     summarise)
from .textutil import sha256


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


WRITER_PARTS = ("screening", "extraction", "summary")
CHECK_PARTS = ("code_checks", "model_check", "check_stamp")


def check_stamp(checker: Model) -> str:
    return f"{checker.name}:{PROMPT_VERSIONS['check']}"


def process(paper: Paper, topic: dict, writer: Model, checker: Model,
            cached: dict | None = None) -> dict:
    """Runs every stage that `cached` does not already hold. The writer's work and
    the checker's work are reused separately, so changing only the checker or its
    prompt re-runs only the checking."""
    entry: dict = {"paper": paper.to_dict()}
    cached = cached or {}
    try:
        if "screening" in cached:
            entry.update({k: cached[k] for k in WRITER_PARTS if k in cached})
        else:
            entry["screening"] = screen(paper, topic, writer)
        if entry["screening"]["decision"] != "include":
            entry["route"] = route(entry["screening"], None, None, None, topic)
            return entry
        if "extraction" not in entry:
            entry["extraction"] = extract(paper, topic, writer)
            entry["summary"] = summarise(entry["extraction"], writer)
        if cached.get("check_stamp") == check_stamp(checker):
            entry.update({k: cached[k] for k in CHECK_PARTS})
        else:
            entry["code_checks"] = code_checks(paper, entry["extraction"], entry["summary"], topic)
            entry["model_check"] = model_check(paper, entry["extraction"], entry["summary"], topic, checker)
            entry["check_stamp"] = check_stamp(checker)
        entry["route"] = route(entry["screening"], entry["code_checks"], entry["model_check"],
                               entry["extraction"], topic)
    except QuotaExhausted:
        raise  # out of budget for today: stop, the cache keeps finished papers
    except Exception as e:  # one bad paper must not stop the run
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["route"] = {"outcome": "review", "problems": 1,
                          "reasons": [f"Processing failed, so a person must check it: {e}"]}
    return entry


class Cache:
    """Processed papers, one file each, keyed by everything that shapes the writer's
    work: the abstract, the writer model, its prompts and the topic definition. The
    checker's work is stored inside with a stamp and redone when the stamp changes.
    A daily 7-day window re-reads mostly the same papers; this makes those free,
    and lets an interrupted run continue where it stopped."""

    def __init__(self, folder: Path | None, topic: dict, writer: Model):
        self.folder = folder
        writer_prompts = {k: v for k, v in PROMPT_VERSIONS.items() if k != "check"}
        self.salt = sha256(json.dumps([writer.name, writer_prompts, topic], sort_keys=True))[:16]
        if folder:
            folder.mkdir(parents=True, exist_ok=True)

    def _path(self, paper: Paper) -> Path:
        return self.folder / f"{paper.pmid}-{paper.abstract_sha256[:8]}-{self.salt}.json"

    def get(self, paper: Paper) -> dict | None:
        if not self.folder or not self._path(paper).exists():
            return None
        entry = json.loads(self._path(paper).read_text(encoding="utf-8"))
        entry["paper"] = paper.to_dict()  # found_by can differ between runs
        return entry

    def put(self, paper: Paper, entry: dict) -> None:
        if self.folder and "error" not in entry:
            self._path(paper).write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")


def run(topic: dict, start: date, end: date, writer: Model, checker: Model,
        pubmed: PubMedClient | None = None, audit_k: int = 5, workers: int = 1,
        limit: int | None = None, cache_dir: Path | None = None) -> dict:
    pubmed = pubmed or PubMedClient()
    started = datetime.now(timezone.utc)
    run_id = f"{topic['id']}-{end.isoformat()}-{started.strftime('%H%M%S')}"

    log(f"Searching PubMed {start} to {end}")
    primary = pubmed.search(topic["primary_query"], start, end)
    secondary = pubmed.search(topic["secondary_query"], start, end)
    pmids = list(dict.fromkeys(primary + secondary))
    if limit:
        pmids = pmids[:limit]
    log(f"  main search {len(primary)}, second search {len(secondary)}, together {len(pmids)}")

    papers = pubmed.fetch(pmids)
    p_set, s_set = set(primary), set(secondary)
    for p in papers:
        p.found_by = [name for name, ids in (("main", p_set), ("second", s_set)) if p.pmid in ids]

    cache = Cache(cache_dir, topic, writer)
    stamp = check_stamp(checker)

    def done(hit: dict | None) -> bool:
        return bool(hit) and (hit.get("screening", {}).get("decision") != "include"
                              or hit.get("check_stamp") == stamp)

    def handle(p: Paper) -> dict:
        hit = cache.get(p)
        if done(hit):
            return hit
        entry = process(p, topic, writer, checker, cached=hit)
        cache.put(p, entry)
        log(f"  {p.pmid}: {entry['route']['outcome']}")
        return entry

    hits = [cache.get(p) for p in papers]
    log(f"Screening and checking {len(papers)} papers ({sum(map(done, hits))} already done, "
        f"{sum(bool(h) and not done(h) for h in hits)} need only the checker)")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = list(pool.map(handle, papers))

    # One more, unhurried pass for papers that failed (usually rate limits).
    failed = [i for i, e in enumerate(entries) if "error" in e]
    if failed:
        log(f"Retrying {len(failed)} papers that failed")
        by_pmid = {p.pmid: p for p in papers}
        for i in failed:
            entries[i] = handle(by_pmid[entries[i]["paper"]["pmid"]])
    still = sum("error" in e for e in entries)
    if still:
        log(f"{still} papers still failed; they are listed for a reviewer")

    # Only papers screened in on their abstract count as relevant for the estimate;
    # records without an abstract go to a reviewer but say nothing about relevance.
    relevant = {e["paper"]["pmid"] for e in entries
                if e.get("screening", {}).get("decision") == "include"}
    excluded = [e["paper"]["pmid"] for e in entries if e["route"]["outcome"] == "excluded"]

    return {
        "run_id": run_id,
        "topic": topic,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "searches": {"main": {"query": topic["primary_query"], "pmids": primary},
                     "second": {"query": topic["secondary_query"], "pmids": secondary}},
        "entries": entries,
        "misses": {
            "capture_recapture": capture_recapture(relevant & p_set, relevant & s_set),
            "audit_sample": audit_sample(excluded, audit_k, seed=run_id),
        },
        "audit": {
            "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "litwatch_version": __version__,
            "python": platform.python_version(),
            "writer_model": writer.name,
            "checker_model": checker.name,
            "prompt_versions": PROMPT_VERSIONS,
            "topic_sha256": sha256(json.dumps(topic, sort_keys=True)),
        },
    }


def recheck(result: dict, checker: Model, save_each=None) -> dict:
    """Re-run the checker, code checks and routing on a saved run, keeping the
    writer's screening, extraction and summaries. Saves after every paper, so an
    interrupted recheck continues where it stopped."""
    topic = result["topic"]
    stamp = f"{checker.name}:{PROMPT_VERSIONS['check']}"
    for entry in result["entries"]:
        if entry.get("screening", {}).get("decision") != "include" or not entry.get("extraction"):
            continue
        if entry.get("rechecked_with") == stamp:
            continue
        paper = Paper(**{k: v for k, v in entry["paper"].items() if k != "url"})
        entry["code_checks"] = code_checks(paper, entry["extraction"], entry["summary"], topic)
        try:
            entry["model_check"] = model_check(paper, entry["extraction"], entry["summary"], topic, checker)
        except QuotaExhausted:
            raise
        except Exception as e:  # keep the old check for this paper rather than stop
            log(f"  {paper.pmid}: recheck failed, keeping the previous check ({e})")
            continue
        entry["route"] = route(entry["screening"], entry["code_checks"], entry["model_check"],
                               entry["extraction"], topic)
        entry["rechecked_with"] = stamp
        log(f"  {paper.pmid}: {entry['route']['outcome']}")
        if save_each:
            save_each(result)
    result["audit"]["checker_model"] = checker.name
    result["audit"]["prompt_versions"] = PROMPT_VERSIONS
    result["audit"]["rechecked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def save(result: dict, out_dir: Path) -> Path:
    folder = out_dir / result["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return folder
