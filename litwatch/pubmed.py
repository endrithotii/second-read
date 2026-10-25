"""Stage 1, Watch: pull newly added PubMed records through NCBI E-utilities.

Plain code, no model. Records are selected by Entrez date (the day PubMed
added them), so a daily run sees each paper once.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .textutil import normalise, sha256, split_sentences

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "litwatch"


@dataclass
class Paper:
    pmid: str
    title: str
    abstract: str
    sentences: list[str]
    journal: str = ""
    year: str = ""
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    publication_types: list[str] = field(default_factory=list)
    abstract_sha256: str = ""
    found_by: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["url"] = self.url
        return d


class PubMedClient:
    def __init__(self, api_key: str | None = None, email: str | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("NCBI_API_KEY")
        self.email = email if email is not None else os.environ.get("NCBI_EMAIL")
        # NCBI allows 3 requests/second without a key and 10 with one.
        self._min_interval = 0.11 if self.api_key else 0.34
        self._last = 0.0

    def _get(self, endpoint: str, params: dict) -> bytes:
        params = dict(params, tool=TOOL)
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        url = f"{EUTILS}/{endpoint}?{urllib.parse.urlencode(params)}"
        for attempt in range(4):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=60) as resp:
                    return resp.read()
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def search(self, query: str, start: date, end: date) -> list[str]:
        """PMIDs added to PubMed between start and end (inclusive) matching query."""
        ids: list[str] = []
        retstart = 0
        while True:
            raw = self._get("esearch.fcgi", {
                "db": "pubmed", "term": query, "retmode": "json",
                "datetype": "edat",
                "mindate": start.strftime("%Y/%m/%d"),
                "maxdate": end.strftime("%Y/%m/%d"),
                "retmax": 500, "retstart": retstart,
            })
            result = json.loads(raw)["esearchresult"]
            batch = result.get("idlist", [])
            ids.extend(batch)
            retstart += len(batch)
            if not batch or retstart >= int(result.get("count", 0)):
                return ids

    def fetch(self, pmids: list[str]) -> list[Paper]:
        papers: list[Paper] = []
        for i in range(0, len(pmids), 100):
            raw = self._get("efetch.fcgi", {
                "db": "pubmed", "id": ",".join(pmids[i:i + 100]), "retmode": "xml",
            })
            root = ET.fromstring(raw)
            papers.extend(_parse(article) for article in root.iter("PubmedArticle"))
        return papers


def _text(el) -> str:
    return normalise("".join(el.itertext())) if el is not None else ""


def _parse(article) -> Paper:
    cit = article.find("MedlineCitation")
    art = cit.find("Article")
    parts = []
    for node in art.findall("Abstract/AbstractText"):
        body = _text(node)
        label = node.get("Label")
        if body:
            parts.append(f"{label.capitalize()}: {body}" if label else body)
    abstract = " ".join(parts)

    year = _text(art.find("Journal/JournalIssue/PubDate/Year")) or \
        _text(art.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
    authors = []
    for a in art.findall("AuthorList/Author"):
        last, initials = _text(a.find("LastName")), _text(a.find("Initials"))
        if last:
            authors.append(f"{last} {initials}".strip())
        elif a.find("CollectiveName") is not None:
            authors.append(_text(a.find("CollectiveName")))
    doi = ""
    for el in art.findall("ELocationID"):
        if el.get("EIdType") == "doi":
            doi = _text(el)
    if not doi:
        for el in article.findall("PubmedData/ArticleIdList/ArticleId"):
            if el.get("IdType") == "doi":
                doi = _text(el)

    return Paper(
        pmid=_text(cit.find("PMID")),
        title=_text(art.find("ArticleTitle")),
        abstract=abstract,
        sentences=split_sentences(abstract),
        journal=_text(art.find("Journal/ISOAbbreviation")) or _text(art.find("Journal/Title")),
        year=year,
        authors=authors,
        doi=doi,
        publication_types=[_text(p) for p in art.findall("PublicationTypeList/PublicationType")],
        abstract_sha256=sha256(abstract),
    )


def window(days: int, end: date | None = None) -> tuple[date, date]:
    end = end or date.today()
    return end - timedelta(days=days - 1), end
