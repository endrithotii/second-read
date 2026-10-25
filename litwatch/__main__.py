"""Command line: python -m litwatch run --topic topics/<file>.json [--days 30]"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .llm import QuotaExhausted, family, make_model
from .pubmed import window
from .report import render
from .stages import clean_omitted, route
from .run import log, recheck, run, save


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="litwatch", description="Daily literature surveillance with checked agents.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="search, screen, extract, check and write a report")
    r.add_argument("--topic", required=True, type=Path)
    r.add_argument("--days", type=int, default=1, help="how many days of newly added papers (default 1)")
    r.add_argument("--end", type=date.fromisoformat, help="last day of the window, YYYY-MM-DD (default today)")
    r.add_argument("--writer", default="anthropic:claude-sonnet-5-5",
                   help="model that screens, extracts and summarises, as provider:model")
    r.add_argument("--checker", default="openai:gpt-4.1",
                   help="model from a different family that audits the writer, as provider:model")
    r.add_argument("--out", type=Path, default=Path("runs"))
    r.add_argument("--audit-sample", type=int, default=5)
    r.add_argument("--limit", type=int, help="process at most this many papers (for a quick try)")
    r.add_argument("--cache", type=Path, default=Path("cache"),
                   help="folder of finished papers, reused by later runs (default ./cache)")
    r.add_argument("--workers", type=int, default=1,
                   help="papers processed at once (default 1, which suits free tiers' rate limits)")

    rr = sub.add_parser("render", help="rebuild report.html from a saved run.json")
    rr.add_argument("run_json", type=Path)
    rr.add_argument("--reroute", action="store_true",
                    help="re-apply the current routing rules first (no model calls)")

    rc = sub.add_parser("recheck", help="re-run only the checker and routing on a saved run.json "
                                         "(after changing the checker or its prompt)")
    rc.add_argument("run_json", type=Path)
    rc.add_argument("--checker", default="groq:qwen/qwen3.8-27b")

    args = ap.parse_args(argv)

    if args.cmd == "render":
        result = json.loads(args.run_json.read_text(encoding="utf-8"))
        if args.reroute:
            # Routing is plain code: apply the current rules to the saved answers.
            for e in result["entries"]:
                if e.get("model_check"):
                    e["model_check"]["omitted"] = clean_omitted(e["model_check"]["omitted"], result["topic"])
                    e["route"] = route(e["screening"], e["code_checks"], e["model_check"],
                                       e["extraction"], result["topic"])
            args.run_json.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        out = args.run_json.with_name("report.html")
        out.write_text(render(result), encoding="utf-8")
        print(out)
        return 0

    if args.cmd == "recheck":
        def write(res):
            args.run_json.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        result = recheck(json.loads(args.run_json.read_text(encoding="utf-8")), make_model(args.checker),
                         save_each=write)
        write(result)
        out = args.run_json.with_name("report.html")
        out.write_text(render(result), encoding="utf-8")
        print(out)
        return 0

    topic = json.loads(args.topic.read_text(encoding="utf-8"))
    try:
        writer, checker = make_model(args.writer), make_model(args.checker)
    except (RuntimeError, ValueError) as e:
        log(f"Cannot start: {e}. See README.md, section 'Run it'.")
        return 2
    if family(args.writer) == family(args.checker):
        log("Warning: writer and checker come from the same model family, so their mistakes "
            "are more likely to line up. Use a different provider for --checker if you can.")
    start, end = window(args.days, args.end)
    try:
        result = run(topic, start, end, writer, checker, audit_k=args.audit_sample, limit=args.limit,
                     workers=args.workers, cache_dir=args.cache)
    except QuotaExhausted as e:
        log(f"Stopped: {e}\nFinished papers are kept in {args.cache}; run again later to continue.")
        return 3
    folder = save(result, args.out)
    (folder / "report.html").write_text(render(result), encoding="utf-8")
    print(folder / "report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
