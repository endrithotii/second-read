"""A small public summary of a run, for other pages to show (e.g. a personal site).

    python -m litwatch.summary site/run.json > site/summary.json
"""

import json
import sys


def summarise_run(result: dict) -> dict:
    entries = result["entries"]
    review = [e for e in entries if e["route"]["outcome"] == "review"]
    cr = result["misses"]["capture_recapture"]
    return {
        "topic": result["topic"]["name"],
        "window": result["window"],
        "papers": len(entries),
        "accepted": sum(e["route"]["outcome"] == "accepted" for e in entries),
        "review": len(review),
        "check_failed": sum(bool(e["route"].get("problems")) for e in review),
        "excluded": sum(e["route"]["outcome"] == "excluded" for e in entries),
        "likely_missed": cr.get("missed"),
        "writer_model": result["audit"]["writer_model"],
        "checker_model": result["audit"]["checker_model"],
        "finished": result["audit"]["finished"],
    }


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(summarise_run(json.load(f)), indent=2))
