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
from .llm import Model
from .misses import audit_sample, capture_recapture
from .pubmed import Paper, PubMedClient
from .stages import (PROMPT_VERSIONS, code_checks, extract, model_check, route, screen,
                     summarise)
from .textutil import sha256


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def process(paper: Paper, topic: dict, writer: Model, checker: Model) -> dict:
    entry: dict = {"paper": paper.to_dict()}
    try:
        entry["screening"] = screen(paper, topic, writer)
        if entry["screening"]["decision"] != "include":
            entry["route"] = route(entry["screening"], None, None, None, topic)
            return entry
        entry["extraction"] = extract(paper, topic, writer)
        entry["summary"] = summarise(entry["extraction"], writer)
        entry["code_checks"] = code_checks(paper, entry["extraction"], entry["summary"], topic)
        entry["model_check"] = model_check(paper, entry["extraction"], entry["summary"], topic, checker)
        entry["route"] = route(entry["screening"], entry["code_checks"], entry["model_check"],
                               entry["extraction"], topic)
    except Exception as e:  # one bad paper must not stop the run
        entry["error"] = f"{type(e).__name__}: {e}"
        entry["route"] = {"outcome": "review", "problems": 1,
                          "reasons": [f"Processing failed, so a person must check it: {e}"]}
    return entry


def run(topic: dict, start: date, end: date, writer: Model, checker: Model,
        pubmed: PubMedClient | None = None, audit_k: int = 5, workers: int = 4,
        limit: int | None = None) -> dict:
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

    log(f"Screening and checking {len(papers)} papers")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = list(pool.map(lambda p: process(p, topic, writer, checker), papers))

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


def save(result: dict, out_dir: Path) -> Path:
    folder = out_dir / result["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "run.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return folder
