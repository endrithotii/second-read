"""Command line: python -m litwatch run --topic topics/<file>.json [--days 30]"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .llm import make_model
from .pubmed import window
from .report import render
from .run import log, run, save


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

    rr = sub.add_parser("render", help="rebuild report.html from a saved run.json")
    rr.add_argument("run_json", type=Path)

    args = ap.parse_args(argv)

    if args.cmd == "render":
        result = json.loads(args.run_json.read_text(encoding="utf-8"))
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
    if writer.name.split("/")[0] == checker.name.split("/")[0]:
        log("Warning: writer and checker come from the same model family, so their mistakes "
            "are more likely to line up. Use a different provider for --checker if you can.")
    start, end = window(args.days, args.end)
    result = run(topic, start, end, writer, checker, audit_k=args.audit_sample, limit=args.limit)
    folder = save(result, args.out)
    (folder / "report.html").write_text(render(result), encoding="utf-8")
    print(folder / "report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
