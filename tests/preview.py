"""Layout preview: real PubMed papers, offline stub models, a banner on the page.

    python tests/preview.py --days 30

Use it to look at the report without API keys. Do not present its
screening or extraction results as real: the stub is keyword matching.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from litwatch.pubmed import window  # noqa: E402
from litwatch.report import render  # noqa: E402
from litwatch.run import run, save  # noqa: E402
from stub import StubModel  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--topic", default="topics/egfr_osimertinib_resistance.json")
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--out", default="runs-preview")
args = ap.parse_args()

topic = json.loads(Path(args.topic).read_text(encoding="utf-8"))
start, end = window(args.days)
result = run(topic, start, end, StubModel("writer"), StubModel("checker"), workers=1)
result["audit"]["banner"] = ("Layout preview: the papers are real, but screening, extraction and "
                             "checking were done by an offline keyword stub, not by language models.")
folder = save(result, Path(args.out))
(folder / "report.html").write_text(render(result), encoding="utf-8")
print(folder / "report.html")
