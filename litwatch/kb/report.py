"""The evidence report page: every number with its interval, failures included."""

from __future__ import annotations

from html import escape


def _e(x) -> str:
    return escape("" if x is None else str(x))


def pct(v) -> str:
    return "n/a" if v is None else f"{100 * v:.0f}%"


def scale(m: dict, label: str, note: str = "", good_high: bool = True, n: int | None = None) -> str:
    """A 0-100% track with the estimate and its 95% interval drawn on it."""
    v, lo, hi = m.get("value"), m.get("low"), m.get("high")
    if v is None:
        return f'<div class="metric"><div class="mlabel">{_e(label)}</div><div class="mvalue none">not measured yet</div><p class="mnote">{_e(note)}</p></div>'
    tone = "good" if (v >= 0.8 if good_high else v <= 0.1) else ("weak" if (v < 0.5 if good_high else v > 0.25) else "mid")
    nn = f", n = {n}" if n is not None else ""
    return f"""<div class="metric {tone}">
  <div class="mlabel">{_e(label)}</div>
  <div class="mvalue">{pct(v)}<small>95% interval {pct(lo)} to {pct(hi)}{nn}</small></div>
  <div class="track" aria-hidden="true"><span class="ci" style="left:{100 * (lo or 0):.1f}%;width:{100 * ((hi or 0) - (lo or 0)):.1f}%"></span><span class="pt" style="left:{100 * v:.1f}%"></span></div>
  <p class="mnote">{_e(note)}</p>
</div>"""


def kappa_row(name: str, k: dict) -> str:
    v = k.get("value")
    word = "n/a" if v is None else ("almost perfect" if v > 0.8 else "substantial" if v > 0.6 else "moderate" if v > 0.4 else "fair" if v > 0.2 else "slight or none")
    rng = "" if k.get("low") is None else f"{k['low']:.2f} to {k['high']:.2f}"
    return f"<tr><td>{_e(name)}</td><td class=num>{'n/a' if v is None else f'{v:.2f}'}</td><td class=num>{rng}</td><td>{word}</td><td class=num>{k.get('n', '')}</td></tr>"


def page(m: dict, sample: list[dict], categories: list[str]) -> str:
    gt, s, sc, ex, tt, pt, pr, osi = (m["ground_truth"], m["search"], m["screening"], m["extraction"],
                                      m["time_travel"], m["pubtator"], m["prisma"], m["osimertinib_resistance"])
    fa = ex.get("field_accuracy", {})
    field_names = {"drugs": "Drugs", "disease": "Disease (same OncoTree branch)", "evidence_type": "Evidence type",
                   "direction": "Direction", "significance": "Clinical significance", "level": "Evidence level (A-E)"}
    fields_html = "".join(scale(fa[f], field_names[f], n=fa[f].get("n")) for f in field_names if f in fa)
    kap = ex.get("kappa", {})
    kappa_html = "".join(kappa_row(n, kap[k]) for k, n in [("evidence_type", "Evidence type"), ("direction", "Direction"),
                                                             ("significance", "Clinical significance"), ("level", "Evidence level")] if k in kap)
    reasons = "".join(f"<li><b>{c}</b> {_e(r)}</li>" for r, c in ex.get("reasons", []))
    tt_rows = "".join(
        f"<tr><td>{_e(r['profile'])}</td><td>{_e(', '.join(r['therapies']))}</td><td>{_e(r['significance'].lower())}</td>"
        f"<td>{_e(r['paper_indexed'])}</td><td>{_e(r['civic_submitted'])}</td>"
        f"<td>{'yes' if r['flagged'] else ('no' if r['processed'] else 'not run yet')}</td>"
        f"<td>{_e(r['label_vs_kb_at_cutoff'] or '')}</td><td class=num>{'' if r['lead_days'] is None else r['lead_days']}</td></tr>"
        for r in tt["rows"])
    errs = "".join(f"<li>PMID {_e(x['pmid'])}, entry {_e(x['entry'])} against CIViC EID{_e(x['civic'])}: wrong {_e(', '.join(x['wrong']))}</li>"
                   for x in ex.get("accepted_errors", []))
    sample_rows = "".join(
        f"<tr><td class=num>{x['item']}</td><td><a href='https://pubmed.ncbi.nlm.nih.gov/{_e(x['pmid'])}/'>{_e(x['pmid'])}</a></td>"
        f"<td>{_e(x['profile'])}</td><td>{_e(x['drugs'])}</td><td>{_e(x['significance'].lower() if x['significance'] else '')}</td><td>{_e(x['level'])}</td></tr>"
        for x in sample)
    labels = ex.get("kb_labels", {})
    label_html = "".join(f"<li><b>{labels.get(k, 0)}</b> {k}</li>" for k in ("new", "supports", "refines", "contradicts"))
    ab = pr.get("abstract_only_reasons", {})
    pts, ptc = pt.get("specific", {}), pt.get("category", {})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>Second Read: evidence entries measured against CIViC</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Red+Hat+Display:wght@500;700;900&family=Red+Hat+Text:ital,wght@0,400;0,500;0,700;1,400&display=swap">
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
  <p class="where"><a href="../">Second Read</a>, knowledge-base mode</p>
  <h1>Resistance to osimertinib in EGFR-mutant lung cancer: evidence entries, measured against expert curators</h1>
  <p class="lede">Papers become structured evidence entries (gene, variant, drug, disease, evidence type, direction, significance, level, and the exact sentence or table cell they rest on), each compared with the knowledge base. Every number below is measured against CIViC's curators on papers held out from prompt development, with a 95% interval.</p>
  <p class="stamp">Generated {_e(m['generated'])}. Ground truth: {gt['items']} CIViC entries from {gt['papers']} papers ({gt['held_out_papers']} papers held out, {gt['dev_papers']} used to build the prompts).</p>
</div></header>
<main class="wrap">

<section class="plain">
  <h2>Read this first</h2>
  <ul>
    <li><b>The narrow topic is too small to measure on its own.</b> CIViC holds {gt['osimertinib_resistance_items']} curated entries on resistance to osimertinib. The evaluation therefore uses all CIViC evidence on EGFR inhibitors in lung cancer, and reports the osimertinib-resistance subset separately, with its wide interval.</li>
    <li><b>CIViC is not complete.</b> An entry CIViC lacks is not automatically wrong, so every precision figure measured against CIViC is a floor. The blind expert review below is what measures it properly.</li>
    <li><b>Most papers have no open full text.</b> {pr.get('abstract_only', 0)} of {pr.get('extracted_papers', 0)} evaluated papers could only be read as abstracts; results are split by source.</li>
    <li><b>Progress:</b> {ex.get('papers', 0)} of {ex.get('papers_planned', 0)} held-out papers extracted, {sc.get('done', 0)} of {sc.get('planned', 0)} screening papers screened. Free model quotas limit how much runs per day; the numbers update as runs complete.</li>
  </ul>
</section>

<section>
  <h2>1. Finding the papers</h2>
  <div class="grid">
    {scale(s['recall_all'], 'Search recall: CIViC papers the topic search finds', f"{s['pool']:,} papers in the search results, all years.")}
    {scale(sc.get('recall', {}), 'Screening recall on held-out CIViC papers', f"{sc.get('positives_screened', 0)} held-out CIViC papers screened. Missed: {', '.join(sc.get('missed', [])[:8]) or 'none'}.")}
    {scale(sc.get('precision', {}), 'Screening precision (floor)', f"{sc.get('negatives_screened', 0)} other papers sampled at random from the search, each standing for {sc.get('negative_weight', 0):.0f}. Papers CIViC never curated count as wrong.", good_high=True)}
    {scale(sc.get('wss95', {}), 'Work saved at 95% recall (WSS@95)', 'Share of the search results a person would not need to read, ranked by the screener, while still finding 95% of the curated papers, minus the 5% allowed to miss.')}
  </div>
</section>

<section>
  <h2>2. Extracting the evidence</h2>
  <div class="grid">
    {scale(ex.get('item_recall', {}), 'Curated entries the system also produced', f"{ex.get('curated_items', 0)} CIViC entries in {ex.get('papers', 0)} held-out papers. Matched on the same molecular profile and at least one drug.", n=ex.get('curated_items'))}
    {scale(ex.get('item_recall_full_text', {}), 'From full text', '', n=ex.get('item_recall_full_text', {}).get('n'))}
    {scale(ex.get('item_recall_abstract_only', {}), 'From the abstract only', '', n=ex.get('item_recall_abstract_only', {}).get('n'))}
    {scale(ex.get('entry_precision_vs_civic', {}), 'System entries that CIViC also holds (floor)', f"{ex.get('entries', 0)} entries produced. The rest are either errors or evidence CIViC has not curated; the expert review tells which.")}
  </div>
  <h3>Field accuracy, on entries matched to a CIViC entry</h3>
  <div class="grid">{fields_html}</div>
  <h3>Writer and checker agreement (Cohen's kappa)</h3>
  <p class="note">The checker comes from a different model family, reads the same cited text and assigns the labels without seeing the writer's.</p>
  <div class="tablewrap"><table><thead><tr><th>Field</th><th>Kappa</th><th>95% interval</th><th>Agreement</th><th>Entries</th></tr></thead><tbody>{kappa_html}</tbody></table></div>
</section>

<section>
  <h2>3. What reaches a person</h2>
  <div class="grid">
    {scale(ex.get('to_person', {}), 'Entries sent to a person', 'Every code check must pass, every name must be normalised, the checker must say the cited text supports the entry and agree on type, direction, significance and level.', good_high=False)}
    {scale(ex.get('accepted_error_rate', {}), 'Errors among automatically accepted entries', f"{ex.get('accepted_matched', 0)} accepted entries could be compared with CIViC; {ex.get('accepted_not_in_civic', 0)} accepted entries are not in CIViC and need the expert review.", good_high=False, n=ex.get('accepted_matched'))}
  </div>
  <div class="two">
    <div><h3>Why entries went to a person</h3><ul class="reasons">{reasons or '<li>None yet.</li>'}</ul></div>
    <div><h3>Accepted entries that disagree with CIViC</h3><ul class="reasons">{errs or '<li>None among the entries that could be compared.</li>'}</ul></div>
  </div>
  <h3>Compared with the knowledge base (other papers only)</h3>
  <ul class="labels">{label_html}</ul>
</section>

<section>
  <h2>4. Resistance to osimertinib</h2>
  <div class="grid">{scale(osi.get('recall', {}), 'Curated osimertinib-resistance entries found', f"{osi.get('evaluated', 0)} of {osi.get('items', 0)} held-out entries evaluated. Too few for a firm number; read the interval.", n=osi.get('evaluated'))}</div>
</section>

<section>
  <h2>5. Time travel: would it have flagged what curators added later?</h2>
  <p class="note">CIViC entries submitted after {_e(tt['cutoff'])}. The system is compared with the knowledge base as it stood on that date. Lead time is the days between the paper entering PubMed, when a daily run would have read it, and a curator submitting the entry.</p>
  <div class="grid">{scale(tt.get('flagged', {}), 'Later CIViC entries the system flagged', f"{tt.get('eligible', 0)} entries from papers that were in PubMed before the cutoff. Median lead time: {tt.get('median_lead_days') if tt.get('median_lead_days') is not None else 'n/a'} days.", n=tt.get('eligible'))}</div>
  <div class="tablewrap"><table><thead><tr><th>Profile</th><th>Drugs</th><th>Significance</th><th>Paper in PubMed</th><th>Curator submitted</th><th>Flagged</th><th>Label at cutoff</th><th>Lead, days</th></tr></thead><tbody>{tt_rows}</tbody></table></div>
</section>

<section>
  <h2>6. Against PubTator3</h2>
  <p class="note">NCBI's PubTator3 tags every variant mention. Of the variants curators recorded from a paper, how many does each find in it?</p>
  <div class="grid">
    {scale(pts.get('system', {}), 'Specific variants (e.g. C797S): this system', f"Found only by this system: {pts.get('only_system', 0)}.", n=pts.get('n'))}
    {scale(pts.get('pubtator', {}), 'Specific variants: PubTator3', f"Found only by PubTator3: {pts.get('only_pubtator', 0)}.", n=pts.get('n'))}
    {scale(ptc.get('system', {}), 'Exon-level and copy-number events: this system', 'Exon 19 deletion, amplification, generic mutation.', n=ptc.get('n'))}
    {scale(ptc.get('pubtator', {}), 'Exon-level and copy-number events: PubTator3', 'PubTator3 does not tag these as variants.', n=ptc.get('n'))}
  </div>
  <p class="note">Variants listed per paper: PubTator3 {pt.get('listed_per_paper', {}).get('pubtator', 0):.1f}, this system {pt.get('listed_per_paper', {}).get('system', 0):.1f}. Share of listed variants that curators recorded: PubTator3 {pct(pt.get('share_listed_that_curators_recorded', {}).get('pubtator'))}, this system {pct(pt.get('share_listed_that_curators_recorded', {}).get('system'))}.</p>
</section>

<section>
  <h2>7. Flow of papers (PRISMA style)</h2>
  <ol class="flow">
    <li><b>{pr['identified_by_search']:,}</b> papers found by the topic search ({pr['civic_papers_found_by_search']} of {pr['civic_papers']} CIViC papers among them)</li>
    <li><b>{pr['screened_sample']}</b> screened in the evaluation sample</li>
    <li><b>{pr['extracted_papers']}</b> held-out papers read for evidence: <b>{pr['full_text']}</b> full text, <b>{pr['abstract_only']}</b> abstract only ({_e('; '.join(f'{v} {k}' for k, v in ab.items()))})</li>
    <li><b>{pr['retracted']}</b> retracted, <b>{pr['with_corrections']}</b> with a published correction</li>
    <li><b>{pr['entries']}</b> evidence entries: <b>{pr['accepted']}</b> accepted automatically, <b>{pr['to_person']}</b> sent to a person</li>
  </ol>
</section>

<section>
  <h2>8. Blind expert review</h2>
  <p class="note">A fixed random sample of {len(sample)} automatically accepted entries, for a molecular pathologist or oncology researcher to judge from the source text alone. The reviewer does not see the models' reasoning or the routing. Error categories are fixed in advance: {_e('; '.join(categories))}. <a href="expert_review.csv">Download the review sheet (CSV)</a>.</p>
  <div class="tablewrap"><table><thead><tr><th>#</th><th>PMID</th><th>Profile</th><th>Drugs</th><th>Significance</th><th>Level</th></tr></thead><tbody>{sample_rows or '<tr><td colspan=6>No accepted entries yet.</td></tr>'}</tbody></table></div>
</section>

</main>
<footer class="wrap foot"><p>Ground truth: <a href="https://civicdb.org">CIViC</a> (CC0). Full text: PubMed Central via NCBI E-utilities; metadata and corrections: Europe PMC; variants checked with VariantValidator; genes HGNC; drugs ChEMBL; diseases OncoTree. Not medical advice.</p></footer>
</body></html>"""


# White for reading, ink blue for text, cobalt for structure. Green marks a
# strong number, sunflower a middling one, rose a weak one, so the eye goes
# to the weaknesses first. The interval is always drawn next to the number.
CSS = """
:root { --paper: #FFFFFF; --wash: #F2F4FD; --ink: #0B1033; --soft: #4B5275; --line: #DDE1F0;
  --cobalt: #2443E8; --sun: #FFD23F; --sun-ink: #6E5300; --green: #13A876; --rose: #E8475B;
  --head: "Red Hat Display", Arial, sans-serif; --body: "Red Hat Text", Arial, sans-serif; }
* { box-sizing: border-box; }
html { background: var(--paper); color: var(--ink); }
body { margin: 0; font: 400 17px/1.6 var(--body); }
a { color: var(--cobalt); text-underline-offset: 3px; }
:focus-visible { outline: 3px solid var(--sun); outline-offset: 2px; }
.wrap { max-width: 76rem; margin-inline: auto; padding-inline: 1.25rem; }
.top { background: var(--cobalt); color: #fff; padding-block: 2.4rem 2.2rem; }
.top a { color: #fff; }
.where { margin: 0 0 0.8rem; font-weight: 500; opacity: 0.9; }
h1 { font: 900 clamp(1.8rem, 4vw, 2.9rem)/1.08 var(--head); margin: 0; max-width: 28ch; letter-spacing: -0.01em; }
.lede { max-width: 70ch; margin: 1rem 0 0.6rem; color: #E3E8FF; }
.stamp { font-size: 0.9rem; color: #CBD3FF; margin: 0; }
h2 { font: 700 1.55rem/1.2 var(--head); margin: 0 0 1rem; }
h3 { font: 700 1.1rem var(--head); margin: 1.6rem 0 0.6rem; }
section { margin-block: 2.8rem; }
.plain { background: #FFF6D1; border-left: 8px solid var(--sun); padding: 1.2rem 1.4rem; }
.plain ul { margin: 0; padding-left: 1.1rem; } .plain li { margin: 0.4rem 0; }
.note { color: var(--soft); max-width: 75ch; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16.5rem, 1fr)); gap: 1rem; }
.metric { background: var(--wash); padding: 1rem 1.1rem 0.9rem; border-top: 6px solid var(--line); }
.metric.good { border-top-color: var(--green); } .metric.mid { border-top-color: var(--sun); } .metric.weak { border-top-color: var(--rose); }
.mlabel { font-weight: 700; line-height: 1.3; }
.mvalue { font: 900 2.2rem/1.1 var(--head); margin: 0.4rem 0 0.5rem; }
.mvalue small { display: block; font: 400 0.85rem var(--body); color: var(--soft); margin-top: 0.2rem; }
.mvalue.none { font-size: 1.1rem; color: var(--soft); }
.track { position: relative; height: 0.8rem; background: #fff; border: 1px solid var(--line); }
.track .ci { position: absolute; top: 0; bottom: 0; background: rgba(36, 67, 232, 0.25); }
.track .pt { position: absolute; top: -0.25rem; width: 3px; height: 1.3rem; background: var(--ink); transform: translateX(-1px); }
.mnote { color: var(--soft); font-size: 0.9rem; margin: 0.5rem 0 0; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.93rem; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--soft); font-weight: 500; }
td.num { font-variant-numeric: tabular-nums; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
.reasons { padding-left: 1.1rem; } .reasons li { margin: 0.25rem 0; } .reasons b { color: var(--cobalt); margin-right: 0.3rem; }
.labels { list-style: none; display: flex; flex-wrap: wrap; gap: 0.6rem; padding: 0; }
.labels li { background: var(--wash); padding: 0.4rem 0.9rem; } .labels b { font: 900 1.3rem var(--head); margin-right: 0.3rem; }
.flow { list-style: none; padding: 0; margin: 0; counter-reset: none; }
.flow li { position: relative; background: var(--wash); border-left: 6px solid var(--cobalt); padding: 0.8rem 1rem; margin: 0 0 1.4rem; max-width: 52rem; }
.flow li:not(:last-child)::after { content: ""; position: absolute; left: 1.4rem; bottom: -1.3rem; border: 0.55rem solid transparent; border-top-color: var(--cobalt); }
.flow b { font: 900 1.2rem var(--head); }
.foot { color: var(--soft); font-size: 0.88rem; padding-block: 1rem 3rem; }
@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
"""
