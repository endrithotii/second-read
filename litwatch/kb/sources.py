"""The raw material: full text as JATS XML, tables cell by cell, and the
paper's correction and retraction history.

- Metadata, open-access status, publication types and corrections come from
  Europe PMC (one request per 100 papers).
- Full text comes from PubMed Central through NCBI E-utilities (efetch,
  db=pmc), which returns JATS XML for the open-access subset and for author
  manuscripts. When PMC holds no text we can use, the record says
  "abstract only" and why.
- Every sentence and every table cell gets a stable id (S12, T2.r3.c1) so an
  evidence entry can point to exactly where it came from.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..textutil import normalise, sha256, split_sentences

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
UA = "second-read/0.2 (+https://github.com/endrithotii/second-read)"
RETRACTED_TYPES = {"retracted publication", "retraction of publication"}
CORRECTION_TYPES = {"retraction in": "retraction", "erratum in": "erratum", "expression of concern in": "expression of concern",
                    "corrected and republished in": "corrected and republished",
                    "retracted and republished in": "retracted and republished"}


def _get(url: str, params: dict, timeout: int = 120) -> bytes:
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            req = urllib.request.Request(full, headers={"user-agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(1.5 * 2 ** attempt)
    raise RuntimeError("unreachable")


@dataclass
class Source:
    pmid: str
    title: str = ""
    journal: str = ""
    published: str = ""        # first publication date
    indexed: str = ""          # date the record entered PubMed / Europe PMC
    pmcid: str = ""
    open_access: bool = False
    pub_types: list[str] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)   # [{"type": "retraction", "pmid": ...}]
    abstract: list[str] = field(default_factory=list)       # sentences
    body: list[dict] = field(default_factory=list)          # [{"id": "S1", "section": "Results", "text": ...}]
    tables: list[dict] = field(default_factory=list)        # [{"id": "T1", "label", "caption", "rows": [[cell, ...]]}]
    supplements: list[dict] = field(default_factory=list)   # [{"id", "label", "href", "caption"}]
    supplement_note: str = ""
    text_source: str = "abstract only"                      # "full text (PMC JATS)" or "abstract only"
    why_abstract_only: str = ""
    text_sha256: str = ""

    @property
    def retracted(self) -> bool:
        return any(t.lower() in RETRACTED_TYPES for t in self.pub_types) or any(
            c["type"] == "retraction" for c in self.corrections)

    def units(self) -> dict[str, str]:
        """Every quotable unit by id: abstract sentences (A1..), body sentences (S1..), table cells (T1.r2.c3)."""
        out = {f"A{i}": s for i, s in enumerate(self.abstract, start=1)}
        out.update({b["id"]: b["text"] for b in self.body})
        for t in self.tables:
            for r, row in enumerate(t["rows"], start=1):
                for c, cell in enumerate(row, start=1):
                    if cell:
                        out[f"{t['id']}.r{r}.c{c}"] = cell
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["retracted"] = self.retracted
        return d


def metadata(pmids: list[str]) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for i in range(0, len(pmids), 100):
        chunk = pmids[i:i + 100]
        q = "(" + " OR ".join(f"EXT_ID:{p}" for p in chunk) + ") AND SRC:MED"
        data = json.loads(_get(EPMC, {"query": q, "resultType": "core", "format": "json", "pageSize": 100}))
        for r in data["resultList"]["result"]:
            pmid = r.get("pmid")
            if not pmid:
                continue
            corr = []
            for c in (r.get("commentCorrectionList") or {}).get("commentCorrection", []):
                kind = CORRECTION_TYPES.get(re.sub(r"\s+", " ", c.get("type", "")).strip().lower())
                if kind:
                    corr.append({"type": kind, "pmid": c.get("id"), "reference": c.get("reference", "")})
            abstract = re.sub(r"<[^>]+>", "", r.get("abstractText") or "")
            out[pmid] = Source(
                pmid=pmid, title=re.sub(r"<[^>]+>", "", r.get("title") or ""),
                journal=((r.get("journalInfo") or {}).get("journal") or {}).get("title", ""),
                published=r.get("firstPublicationDate", ""), indexed=r.get("firstIndexDate", ""),
                pmcid=r.get("pmcid", ""), open_access=r.get("isOpenAccess") == "Y",
                pub_types=(r.get("pubTypeList") or {}).get("pubType", []), corrections=corr,
                abstract=split_sentences(abstract))
        time.sleep(0.2)
    return out


def _strip_refs(el: ET.Element) -> None:
    """Remove reference markers in place, keeping the text that follows them."""
    for parent in list(el.iter()):
        kept: list[ET.Element] = []
        for k in list(parent):
            if k.tag == "xref" and k.get("ref-type") in ("bibr", "fn", "table-fn"):
                tail = k.tail or ""
                if kept:
                    kept[-1].tail = (kept[-1].tail or "") + tail
                else:
                    parent.text = (parent.text or "") + tail
                parent.remove(k)
            else:
                kept.append(k)


def _flat(el: ET.Element | None) -> str:
    return normalise("".join(el.itertext())) if el is not None else ""


def parse_jats(xml: bytes, src: Source) -> Source:
    root = ET.fromstring(xml)
    article = root.find(".//article") if root.tag != "article" else root
    if article is None:
        raise ValueError("no <article> in the response")
    body = article.find("body")
    if body is None or not "".join(body.itertext()).strip():
        src.why_abstract_only = "PMC record has no body text (not in the open-access subset or author manuscripts)"
        return src
    _strip_refs(article)
    # Tables first, then remove them so their text is not read twice as sentences.
    tables = []
    for k, tw in enumerate(article.iter("table-wrap"), start=1):
        rows = []
        for tr in tw.iter("tr"):
            rows.append([_flat(td) for td in tr if td.tag in ("td", "th")])
        tables.append({"id": f"T{k}", "label": _flat(tw.find("label")), "caption": _flat(tw.find("caption")),
                       "rows": rows, "footer": _flat(tw.find("table-wrap-foot"))})
    for parent in list(article.iter()):
        for kid in list(parent):
            if kid.tag in ("table-wrap", "fig"):
                parent.remove(kid)
    sentences, n = [], 0
    for sec in body.iter("sec"):
        title = _flat(sec.find("title"))
        for p in sec.findall("p"):
            for s in split_sentences(_flat(p)):
                n += 1
                sentences.append({"id": f"S{n}", "section": title, "text": s})
    if not sentences:  # some articles have paragraphs directly under <body>
        for p in body.iter("p"):
            for s in split_sentences(_flat(p)):
                n += 1
                sentences.append({"id": f"S{n}", "section": "", "text": s})
    sups = []
    for k, sm in enumerate(article.iter("supplementary-material"), start=1):
        media = sm.find(".//media")
        href = media.get("{http://www.w3.org/1999/xlink}href") if media is not None else sm.get("{http://www.w3.org/1999/xlink}href")
        sups.append({"id": f"SUP{k}", "label": _flat(sm.find("label")), "caption": _flat(sm.find("caption")), "href": href or ""})
    src.body, src.tables, src.supplements = sentences, tables, sups
    src.text_source = "full text (PMC JATS)"
    src.why_abstract_only = ""
    return src


def full_text(src: Source, cache: Path) -> Source:
    cache.mkdir(parents=True, exist_ok=True)
    if not src.pmcid:
        src.why_abstract_only = "no PubMed Central copy"
    else:
        path = cache / f"{src.pmcid}.xml"
        if not path.exists():
            path.write_bytes(_get(EUTILS, {"db": "pmc", "id": src.pmcid.replace("PMC", ""), "rettype": "xml",
                                           "tool": "second-read"}))
            time.sleep(0.35)
        try:
            parse_jats(path.read_bytes(), src)
        except (ET.ParseError, ValueError) as e:
            src.why_abstract_only = f"PMC XML could not be read: {e}"
        if src.open_access and src.supplements:
            from .supplements import tables as supp_tables
            try:
                found, src.supplement_note = supp_tables(src.pmcid, cache.parent / "supp")
            except OSError as e:
                found, src.supplement_note = [], f"supplementary download failed: {e}"
            for k, t in enumerate(found, start=1):
                src.tables.append({"id": f"ST{k}", "label": f"Supplementary table {k}", "caption": t["file"],
                                   "rows": t["rows"], "footer": ""})
        elif src.supplements:
            src.supplement_note = "supplementary files exist but the article is not open access, so they were not downloaded"
    text = "\n".join(src.abstract + [b["text"] for b in src.body] +
                     [" | ".join(r) for t in src.tables for r in t["rows"]])
    src.text_sha256 = sha256(text)
    return src


def load_all(pmids: list[str], cache: Path, log=print) -> dict[str, Source]:
    """Metadata plus full text for every PMID, cached on disk as JSON."""
    store = cache / "sources"
    store.mkdir(parents=True, exist_ok=True)
    out, todo = {}, []
    for p in pmids:
        f = store / f"{p}.json"
        if f.exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            d.pop("retracted", None)
            out[p] = Source(**d)
        else:
            todo.append(p)
    if todo:
        meta = metadata(todo)
        for i, p in enumerate(todo, start=1):
            src = meta.get(p) or Source(pmid=p, why_abstract_only="not found in Europe PMC")
            if p in meta:
                full_text(src, cache / "jats")
            (store / f"{p}.json").write_text(json.dumps(src.to_dict(), ensure_ascii=False), encoding="utf-8")
            out[p] = src
            if i % 25 == 0:
                log(f"  sources {i}/{len(todo)}")
    return out
