"""Renders a run as one self-contained HTML page for the reviewer."""

from __future__ import annotations

from html import escape

STAGES = ["Found", "Screened", "Extracted", "Summarised", "Verified", "Routed"]

OUTCOME_ORDER = ["problem", "review", "accepted", "excluded"]
OUTCOME_LABEL = {
    "problem": "Needs a reviewer: a check failed",
    "review": "Needs a reviewer: by policy or unclear",
    "accepted": "Accepted: every check passed",
    "excluded": "Excluded at screening",
}


def _e(x) -> str:
    return escape("" if x is None else str(x))


def _bucket(entry: dict) -> str:
    r = entry["route"]
    if r["outcome"] == "review":
        return "problem" if r.get("problems") else "review"
    return r["outcome"]


def _cite(p: dict) -> str:
    bits = [b for b in (p.get("journal"), p.get("year")) if b]
    return ", ".join(bits)


# ------------------------------------------------------------------ pileup --

def _pileup(entries: list[dict]) -> str:
    groups = {k: [e for e in entries if _bucket(e) == k] for k in OUTCOME_ORDER}
    bar_h, gap, group_gap = 7, 3, 16
    width = 1000
    col = width / len(STAGES)
    y = 0
    bars, labels = [], []
    for key in OUTCOME_ORDER:
        items = groups[key]
        if not items:
            continue
        top = y
        for e in items:
            p = e["paper"]
            # A bar runs only as far as the paper actually got.
            reach = len(STAGES) if e.get("model_check") else 2
            w = col * reach - 6
            title = f"PMID {p['pmid']}: {p['title']}"
            bars.append(
                f'<a href="#p{_e(p["pmid"])}"><rect class="bar {key}" x="3" y="{y}" '
                f'width="{w:.1f}" height="{bar_h}" rx="1.5"><title>{_e(title)}</title></rect></a>')
            y += bar_h + gap
        mid = top + (y - gap - top) / 2
        labels.append((key, len(items), top, y - gap, mid))
        y += group_gap
    height = max(y - group_gap, 1)
    grid = "".join(
        f'<line class="tick" x1="{col * i:.1f}" x2="{col * i:.1f}" y1="0" y2="{height}"/>'
        for i in range(1, len(STAGES)))
    svg = (f'<svg class="pileup-svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
           f'role="img" aria-label="Each paper as one bar through the stages it reached">'
           f'{grid}{"".join(bars)}</svg>')
    legend = "".join(
        f'<li class="{k}" style="--top:{top}px;--h:{bottom - top}px">'
        f'<span class="count">{n}</span> {_e(OUTCOME_LABEL[k])}</li>'
        for k, n, top, bottom, _ in labels)
    heads = "".join(f"<span>{s}</span>" for s in STAGES)
    return (f'<figure class="pileup"><div class="stage-heads" aria-hidden="true">{heads}</div>'
            f'<div class="pileup-body" style="--height:{height}px">{svg}'
            f'<ul class="pileup-legend">{legend}</ul></div>'
            f'<figcaption>Every paper from both searches is one bar, stacked like reads at a locus. '
            f'A bar runs as far as the paper got. Select a bar to jump to that paper.</figcaption></figure>')


# ------------------------------------------------------------------ papers --

def _verdict(v: str) -> str:
    text = {"supported": "Supported", "contradicted": "Contradicted", "beyond": "Not in the abstract",
            "pass": "Passed", "fail": "Failed"}[v]
    return f'<span class="v v-{v}">{text}</span>'


def _paper_head(e: dict) -> str:
    p = e["paper"]
    found = " and ".join(p.get("found_by") or []) or "neither"
    return (f'<h3><a href="{_e(p["url"])}">{_e(p["title"]) or "Untitled record"}</a></h3>'
            f'<p class="cite">{_e(_cite(p))}. PMID {_e(p["pmid"])}. Found by the {found} search.</p>')


def _fields_table(e: dict, topic: dict) -> str:
    fields = e["extraction"]["fields"]
    checks = e["code_checks"]["fields"]
    verdicts = {c["id"]: c for c in e["model_check"]["claims"] if c["kind"] == "field"}
    rows = []
    for f in topic["fields"]:
        item = fields.get(f["id"]) or {}
        if not item.get("value"):
            continue
        cc = checks.get(f["id"], {"passed": True, "problems": []})
        mv = verdicts.get(f["id"], {"verdict": "beyond"})
        code_cell = _verdict("pass") if cc["passed"] else (
            _verdict("fail") + "".join(f'<span class="why">{_e(x)}</span>' for x in cc["problems"]))
        quote = f'<q>{_e(item.get("quote"))}</q>' if item.get("quote") else '<span class="none">No quote</span>'
        rows.append(
            f'<tr><th scope="row">{_e(f["id"].replace("_", " "))}</th>'
            f'<td>{_e(item["value"])}</td><td class="src">{quote}</td>'
            f'<td>{code_cell}</td><td>{_verdict(mv["verdict"])}</td></tr>')
    if not rows:
        return '<p class="none">Nothing was extracted.</p>'
    return ('<div class="table-wrap"><table class="fields"><thead><tr><th scope="col">Field</th>'
            '<th scope="col">Extracted value</th><th scope="col">Quote from the paper</th>'
            '<th scope="col">Code check</th><th scope="col">Second model</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def _summary_block(e: dict) -> str:
    claims = [c for c in e["model_check"]["claims"] if c["kind"] == "summary"]
    sc = e["code_checks"]["summary"]
    sentences = "".join(
        f'<span class="s s-{c["verdict"]}">{_e(c["text"])}</span> ' for c in claims)
    notes = "".join(f'<li>{_e(c["id"].capitalize())}: {_verdict(c["verdict"])}</li>' for c in claims)
    if not sc["passed"]:
        notes += "".join(f"<li>Code check: {_verdict('fail')} {_e(x)}</li>" for x in sc["problems"])
    return (f'<div class="summary"><p class="summary-text">{sentences}</p>'
            f'<p class="by">Written by {_e(e["summary"]["model"])} from the extracted fields only. '
            f'It never saw the abstract.</p><ul class="summary-checks">{notes}</ul></div>')


def _paper(e: dict, topic: dict, open_: bool) -> str:
    p = e["paper"]
    reasons = "".join(f"<li>{_e(r)}</li>" for r in e["route"]["reasons"])
    notes = "".join(f"<li>{_e(n)}</li>" for n in e["route"].get("notes", []))
    body = [f'<ul class="reasons">{reasons}</ul>' if reasons else "",
            f'<ul class="notes">{notes}</ul>' if notes else ""]
    if "error" in e:
        body.append(f'<p class="none">Error: {_e(e["error"])}</p>')
    if e.get("extraction") and e.get("model_check"):
        body.append(_summary_block(e))
        body.append(_fields_table(e, topic))
        if e["model_check"]["omitted"]:
            om = "".join(f"<li>{_e(o)}</li>" for o in e["model_check"]["omitted"])
            body.append(f'<div class="omitted"><h4>Findings the second model says the record leaves out</h4><ul>{om}</ul></div>')
    if e.get("screening", {}).get("criteria"):
        crit = "".join(
            f'<li><span class="crit-id">{_e(c["id"].replace("_", " "))}</span> '
            f'<span class="ans ans-{c["answer"]}">{_e(c["answer"])}</span>'
            + (f' <q>{_e(c["evidence"])}</q>' if c.get("evidence") else "") + "</li>"
            for c in e["screening"]["criteria"])
        body.append(f'<details class="screening"><summary>How it was screened</summary><ul>{crit}</ul></details>')
    return (f'<article class="paper {_bucket(e)}" id="p{_e(p["pmid"])}">'
            f'{_paper_head(e)}{"".join(body)}</article>') if open_ else (
            f'<details class="paper-fold" id="p{_e(p["pmid"])}"><summary>{_e(p["title"])}'
            f'<span class="cite"> {_e(_cite(p))}</span></summary>'
            f'<article class="paper {_bucket(e)}">{_paper_head(e)}{"".join(body)}</article></details>')


# ------------------------------------------------------------------ misses --

def _misses(result: dict, by_pmid: dict) -> str:
    cr = result["misses"]["capture_recapture"]
    sample = result["misses"]["audit_sample"]
    if cr.get("estimate") is None:
        estimate = f'<p>{_e(cr["note"])}</p>'
    else:
        estimate = (
            f'<p class="lede">The main search found <b>{cr["primary"]}</b> relevant papers and the second, '
            f'independently built search found <b>{cr["secondary"]}</b>. <b>{cr["both"]}</b> were found by both. '
            f'That overlap puts the total number of relevant papers at about <b>{round(cr["estimate"])}</b> '
            f'(range {cr["low"]} to {cr["high"]}). Together the searches found {cr["found"]}, so about '
            f'<b>{cr["missed"]}</b> relevant {"paper was" if cr["missed"] == 1 else "papers were"} likely missed by both.</p>'
            f'<p class="note">{_e(cr["note"])}</p>')
    only = "".join(
        f'<li><a href="#p{_e(x)}">{_e(by_pmid[x]["paper"]["title"])}</a></li>'
        for x in cr["only_secondary"] if x in by_pmid)
    only_block = (f'<h3>Relevant papers only the second search found</h3>'
                  f'<p>These are confirmed misses of the main search, and candidates for improving its query.</p>'
                  f'<ul class="plain">{only}</ul>') if only else ""
    rows = "".join(
        f'<li><label><input type="checkbox"> <a href="{_e(by_pmid[x]["paper"]["url"])}">'
        f'{_e(by_pmid[x]["paper"]["title"])}</a></label>'
        f'<span class="why">Excluded because: {_e(by_pmid[x]["route"]["reasons"][0])}</span></li>'
        for x in sample if x in by_pmid)
    sample_block = (f'<h3>Check these excluded papers by hand</h3>'
                    f'<p>A random sample of what screening threw out. Every wrong exclusion found here is a '
                    f'measured miss, and becomes a test case.</p><ul class="audit">{rows}</ul>') if rows else ""
    return estimate + only_block + sample_block


# ------------------------------------------------------------------- page --

def render(result: dict) -> str:
    topic = result["topic"]
    entries = result["entries"]
    by_pmid = {e["paper"]["pmid"]: e for e in entries}
    buckets = {k: [e for e in entries if _bucket(e) == k] for k in OUTCOME_ORDER}
    to_review = buckets["problem"] + buckets["review"]
    audit = result["audit"]
    w = result["window"]

    def n(k):
        return len(buckets[k])

    review_n = n("problem") + n("review")
    status = (f'{review_n} {"paper needs" if review_n == 1 else "papers need"} a reviewer. '
              f'{n("accepted")} accepted with every check passed. '
              f'{n("excluded")} excluded at screening.')

    excluded_rows = "".join(
        f'<tr><td><a href="{_e(e["paper"]["url"])}">{_e(e["paper"]["title"])}</a></td>'
        f'<td>{_e(e["route"]["reasons"][0])}</td></tr>' for e in buckets["excluded"])
    hashes = "".join(
        f'<tr><td>{_e(e["paper"]["pmid"])}</td><td class="hash">{_e(e["paper"]["abstract_sha256"][:16])}</td></tr>'
        for e in entries)
    prompts = "".join(f"<dt>{_e(k)} prompt</dt><dd class='hash'>{_e(v)}</dd>"
                      for k, v in audit["prompt_versions"].items())
    banner = (f'<p class="banner">{_e(audit["banner"])}</p>' if audit.get("banner") else "")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Second Read: {_e(topic["name"])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&family=Spectral:ital,wght@0,400;1,400&display=swap">
<style>{CSS}</style>
</head>
<body>
{banner}
<header class="top">
  <div class="wrap">
    <p class="product"><b>Second Read</b>, daily literature surveillance with an independent check</p>
    <h1>{_e(topic["name"])}</h1>
    <p class="question">{_e(topic["question"])}</p>
    <p class="status">{_e(status)}</p>
    <dl class="facts">
      <div><dt>Papers added to PubMed</dt><dd>{_e(w["start"])} to {_e(w["end"])}</dd></div>
      <div><dt>Writer model</dt><dd>{_e(audit["writer_model"])}</dd></div>
      <div><dt>Checker model</dt><dd>{_e(audit["checker_model"])}</dd></div>
      <div><dt>Run</dt><dd>{_e(result["run_id"])}</dd></div>
    </dl>
  </div>
</header>

<main class="wrap">
<section>
  {_pileup(entries)}
</section>

<section id="review">
  <h2>Needs a reviewer</h2>
  <p class="section-note">Papers where a check failed come first. Each flag names the field and what went wrong, so the reviewer goes straight to it.</p>
  {"".join(_paper(e, topic, True) for e in to_review) or '<p class="none">Nothing needs a reviewer in this run.</p>'}
</section>

<section id="accepted">
  <h2>Accepted</h2>
  <p class="section-note">Every quote was found in the abstract, every number matched, and the second model supported every claim.</p>
  {"".join(_paper(e, topic, False) for e in buckets["accepted"]) or '<p class="none">No paper passed every check in this run.</p>'}
</section>

<section id="missed">
  <h2>What we may have missed</h2>
  {_misses(result, by_pmid)}
</section>

<section id="excluded">
  <h2>Excluded at screening</h2>
  <details><summary>Show all {n("excluded")} with the reason</summary>
  <div class="table-wrap"><table class="excluded"><thead><tr><th scope="col">Paper</th><th scope="col">Reason</th></tr></thead>
  <tbody>{excluded_rows}</tbody></table></div></details>
</section>

<section id="audit">
  <h2>Audit trail</h2>
  <dl class="audit-list">
    <dt>Started</dt><dd>{_e(audit["started"])}</dd>
    <dt>Finished</dt><dd>{_e(audit["finished"])}</dd>
    <dt>Software</dt><dd>litwatch {_e(audit["litwatch_version"])}, Python {_e(audit["python"])}</dd>
    <dt>Topic definition</dt><dd class="hash">{_e(audit["topic_sha256"][:16])}</dd>
    {prompts}
    <dt>Main search</dt><dd><code>{_e(result["searches"]["main"]["query"])}</code></dd>
    <dt>Second search</dt><dd><code>{_e(result["searches"]["second"]["query"])}</code></dd>
  </dl>
  <details><summary>Fingerprint of every abstract this run read</summary>
  <div class="table-wrap"><table class="hashes"><thead><tr><th scope="col">PMID</th><th scope="col">SHA-256 of the abstract</th></tr></thead>
  <tbody>{hashes}</tbody></table></div></details>
  <p class="section-note">The full record, including every model answer, is in run.json next to this page.</p>
</section>
</main>
<footer class="foot"><div class="wrap">
  <p>A research demonstration by Endrit Hoti. It reads public PubMed titles and abstracts automatically.
  It is not medical advice, and every result is meant for review by a qualified expert.</p>
</div></footer>
<script>{JS}</script>
</body>
</html>"""


JS = """
document.querySelectorAll('.pileup a').forEach(function (a) {
  a.addEventListener('click', function () {
    var t = document.querySelector(a.getAttribute('href'));
    if (t && t.tagName === 'DETAILS') t.open = true;
  });
});
"""

CSS = """
:root {
  --paper: #EEF2F0;
  --sheet: #F8FAF9;
  --ink: #1B262B;
  --muted: #55646A;
  --rule: #C9D3D0;
  --petrol: #24657A;
  --ochre: #8A5F00;
  --ochre-bar: #C9922B;
  --carmine: #A3263F;
  --excluded: #B4C0BD;
  --text: "Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif;
  --source: "Spectral", Georgia, "Times New Roman", serif;
}
* { box-sizing: border-box; }
html { background: var(--paper); color: var(--ink); }
body { margin: 0; font: 17px/1.55 var(--text); font-variant-numeric: tabular-nums; }
a { color: var(--petrol); text-underline-offset: 0.15em; }
a:focus-visible, summary:focus-visible, input:focus-visible { outline: 3px solid var(--petrol); outline-offset: 2px; }
.wrap { max-width: 72rem; margin: 0 auto; padding: 0 1.5rem; }

.banner { margin: 0; padding: 0.7rem 1.5rem; background: var(--ochre); color: #FFF8EC; font-weight: 700; text-align: center; }

.top { border-bottom: 1px solid var(--rule); padding: 3rem 0 2rem; }
.product { margin: 0 0 0.75rem; color: var(--muted); }
h1 { font-size: clamp(2rem, 4vw, 3rem); line-height: 1.1; letter-spacing: -0.01em; margin: 0; max-width: 22ch; }
.question { max-width: 62ch; margin: 1rem 0 0; color: var(--muted); }
.status { margin: 1.75rem 0 0; font-size: 1.3rem; font-weight: 700; max-width: 50ch; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 1rem 2rem; margin: 2rem 0 0; }
.facts dt { color: var(--muted); font-size: 0.85rem; }
.facts dd { margin: 0.1rem 0 0; overflow-wrap: anywhere; }

section { padding: 2.75rem 0; border-bottom: 1px solid var(--rule); }
section:last-of-type { border-bottom: 0; }
h2 { font-size: 1.6rem; line-height: 1.2; margin: 0 0 0.5rem; }
h3 { font-size: 1.15rem; line-height: 1.3; margin: 0; }
h3 a { color: var(--ink); text-decoration-color: var(--rule); }
h3 a:hover { text-decoration-color: var(--ink); }
h4 { font-size: 1rem; margin: 0 0 0.4rem; }
.section-note, .note { color: var(--muted); max-width: 64ch; margin: 0 0 1.5rem; }
.lede { font-size: 1.15rem; max-width: 64ch; }
.none { color: var(--muted); font-style: italic; }

/* pileup: the one memorable thing */
.pileup { margin: 0; }
.stage-heads { display: grid; grid-template-columns: repeat(6, 1fr); margin-right: 19rem; color: var(--muted); font-size: 0.85rem; }
.stage-heads span { padding: 0 0 0.5rem 0.35rem; border-left: 1px solid var(--rule); }
.pileup-body { position: relative; display: grid; grid-template-columns: 1fr 18rem; gap: 1rem; }
.pileup-svg { width: 100%; height: var(--height); display: block; }
.tick { stroke: var(--rule); stroke-width: 1; vector-effect: non-scaling-stroke; }
.bar { transition: opacity 0.15s; }
.bar.accepted { fill: var(--petrol); }
.bar.review { fill: var(--ochre-bar); }
.bar.problem { fill: var(--carmine); }
.bar.excluded { fill: var(--excluded); }
.pileup a:hover .bar, .pileup a:focus .bar { opacity: 0.6; }
.pileup-legend { list-style: none; margin: 0; padding: 0; position: relative; height: var(--height); }
.pileup-legend li { position: absolute; top: var(--top); min-height: var(--h); padding-left: 0.9rem; border-left: 3px solid; font-size: 0.95rem; line-height: 1.3; display: flex; align-items: center; }
.pileup-legend li.accepted { border-color: var(--petrol); }
.pileup-legend li.review { border-color: var(--ochre-bar); }
.pileup-legend li.problem { border-color: var(--carmine); }
.pileup-legend li.excluded { border-color: var(--excluded); }
.pileup-legend .count { font-weight: 700; font-size: 1.4rem; margin-right: 0.5rem; }
figcaption { color: var(--muted); font-size: 0.9rem; margin-top: 1rem; max-width: 64ch; }
@media (prefers-reduced-motion: no-preference) {
  .bar { transform-box: fill-box; transform-origin: left; animation: grow 0.9s cubic-bezier(.2,.7,.2,1) both; }
  @keyframes grow { from { transform: scaleX(0.02); } to { transform: scaleX(1); } }
}

/* papers */
.paper { background: var(--sheet); border: 1px solid var(--rule); border-left: 4px solid var(--rule); padding: 1.5rem 1.75rem; margin: 0 0 1.25rem; }
.paper.problem { border-left-color: var(--carmine); }
.paper.review { border-left-color: var(--ochre-bar); }
.paper.accepted { border-left-color: var(--petrol); }
.cite { color: var(--muted); margin: 0.3rem 0 0; font-size: 0.92rem; }
.reasons { margin: 1rem 0 0; padding-left: 1.2rem; }
.paper.problem .reasons li::marker { color: var(--carmine); }
.reasons li { margin: 0.2rem 0; }
.notes { margin: 0.5rem 0 0; padding-left: 1.2rem; color: var(--muted); font-size: 0.92rem; }
.summary { margin: 1.25rem 0 0; }
.summary-text { margin: 0; font-size: 1.05rem; max-width: 68ch; }
.s-contradicted { background: linear-gradient(transparent 62%, #F2C9D1 62%); }
.s-beyond { background: linear-gradient(transparent 62%, #F1DDB0 62%); }
.by { color: var(--muted); font-size: 0.88rem; margin: 0.35rem 0 0; }
.summary-checks { list-style: none; padding: 0; margin: 0.5rem 0 0; display: flex; flex-wrap: wrap; gap: 0.25rem 1.5rem; font-size: 0.9rem; }

q, .src q { font-family: var(--source); font-size: 1.04em; quotes: "\\201C" "\\201D"; }
.table-wrap { overflow-x: auto; margin: 1.25rem 0 0; }
table { border-collapse: collapse; width: 100%; font-size: 0.93rem; }
th, td { text-align: left; vertical-align: top; padding: 0.55rem 0.75rem 0.55rem 0; border-bottom: 1px solid var(--rule); }
thead th { color: var(--muted); font-weight: 400; font-size: 0.85rem; border-bottom-color: var(--ink); }
tbody th { font-weight: 700; white-space: nowrap; }
.fields td.src { min-width: 16rem; }
.why { display: block; color: var(--carmine); font-size: 0.85rem; margin-top: 0.15rem; }
.audit .why { color: var(--muted); }

.v { display: inline-flex; align-items: center; gap: 0.4rem; font-weight: 700; white-space: nowrap; }
.v::before { content: ""; width: 0.55rem; height: 0.55rem; background: currentColor; }
.v-supported, .v-pass { color: var(--petrol); }
.v-contradicted, .v-fail { color: var(--carmine); }
.v-beyond { color: var(--ochre); }
.v-beyond::before { background: none; border: 2px solid currentColor; width: 0.55rem; height: 0.55rem; }

.omitted { margin: 1.25rem 0 0; padding: 0.9rem 1.1rem; border: 1px dashed var(--ochre); }
.omitted ul { margin: 0; padding-left: 1.2rem; }

details { margin: 1rem 0 0; }
summary { cursor: pointer; color: var(--petrol); }
.screening ul { list-style: none; padding: 0; margin: 0.6rem 0 0; }
.screening li { margin: 0.35rem 0; }
.crit-id { display: inline-block; min-width: 8rem; font-weight: 700; }
.ans { font-weight: 700; }
.ans-yes { color: var(--petrol); } .ans-no { color: var(--carmine); } .ans-unclear { color: var(--ochre); }

.paper-fold { background: var(--sheet); border: 1px solid var(--rule); border-left: 4px solid var(--petrol); margin: 0 0 0.6rem; padding: 0.8rem 1.1rem; }
.paper-fold > summary { color: var(--ink); font-weight: 700; }
.paper-fold > summary .cite { font-weight: 400; }
.paper-fold .paper { border: 0; padding: 0.5rem 0 0; margin: 0; }

ul.plain { padding-left: 1.2rem; }
.audit { list-style: none; padding: 0; }
.audit li { padding: 0.6rem 0; border-bottom: 1px solid var(--rule); }
.audit input { accent-color: var(--petrol); width: 1.05rem; height: 1.05rem; margin-right: 0.5rem; vertical-align: -0.15rem; }

.audit-list { display: grid; grid-template-columns: 12rem 1fr; gap: 0.5rem 1.5rem; margin: 0; }
.audit-list dt { color: var(--muted); }
.audit-list dd { margin: 0; overflow-wrap: anywhere; }
.hash { font-variant-numeric: tabular-nums; letter-spacing: 0.02em; }
code { font-size: 0.85rem; overflow-wrap: anywhere; }

.foot { border-top: 1px solid var(--rule); padding: 1.5rem 0 3rem; color: var(--muted); font-size: 0.9rem; }
.foot p { max-width: 64ch; margin: 0; }

@media (max-width: 760px) {
  body { font-size: 16px; }
  .pileup-body { grid-template-columns: 1fr; }
  .stage-heads { margin-right: 0; font-size: 0.7rem; }
  .pileup-legend { height: auto; }
  .pileup-legend li { position: static; min-height: 0; margin: 0.3rem 0; }
  .paper { padding: 1.1rem; }
  .audit-list { grid-template-columns: 1fr; }
  .audit-list dd { margin-bottom: 0.6rem; }
}
@media print {
  html { background: #fff; }
  .paper, .paper-fold { break-inside: avoid; background: #fff; }
  details { display: block; }
}
"""
