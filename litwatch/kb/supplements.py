"""Supplementary tables, where the licence allows them to be downloaded.

Europe PMC serves supplementary files as one zip for open-access articles
only. From that zip, tables in .xlsx, .docx and .csv files are read cell by
cell with the standard library. PDFs, slides and images are listed but not
read: their tables would need OCR, which this version does not do.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from ..textutil import normalise

URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/supplementaryFiles"
UA = "second-read/0.2 (+https://github.com/endrithotii/second-read)"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _docx_tables(data: bytes) -> list[list[list[str]]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for tbl in root.iter(W + "tbl"):
        rows = []
        for tr in tbl.iter(W + "tr"):
            rows.append([normalise("".join(t.text or "" for t in tc.iter(W + "t"))) for tc in tr.iter(W + "tc")])
        out.append(rows)
    return out


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx_tables(data: bytes, max_rows: int = 400) -> list[list[list[str]]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(S + "si"):
                shared.append(normalise("".join(t.text or "" for t in si.iter(S + "t"))))
        out = []
        for name in sorted(n for n in names if n.startswith("xl/worksheets/sheet")):
            rows = []
            for row in ET.fromstring(z.read(name)).iter(S + "row"):
                cells: dict[int, str] = {}
                for c in row.iter(S + "c"):
                    v = c.find(S + "v")
                    if v is None:
                        is_ = c.find(S + "is")
                        val = normalise("".join(t.text or "" for t in is_.iter(S + "t"))) if is_ is not None else ""
                    else:
                        val = shared[int(v.text)] if c.get("t") == "s" else (v.text or "")
                    cells[_col(c.get("r", "A1"))] = val
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
                if len(rows) >= max_rows:
                    break
            out.append(rows)
    return out


def tables(pmcid: str, cache: Path) -> tuple[list[dict], str]:
    """Tables from the supplementary zip, and a note on what was or was not read."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{pmcid}.zip"
    if not path.exists():
        req = urllib.request.Request(URL.format(pmcid=pmcid), headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=180) as r:
            path.write_bytes(r.read())
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return [], "supplementary files not available for download"
    out, read, skipped = [], [], []
    for name in z.namelist():
        ext = name.rsplit(".", 1)[-1].lower()
        try:
            if ext == "docx":
                found = _docx_tables(z.read(name))
            elif ext == "xlsx":
                found = _xlsx_tables(z.read(name))
            elif ext == "csv":
                found = [list(csv.reader(io.StringIO(z.read(name).decode("utf-8", "replace"))))[:400]]
            else:
                skipped.append(name)
                continue
        except (KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
            skipped.append(name)
            continue
        read.append(name)
        for rows in found:
            if rows:
                out.append({"file": name, "rows": [[normalise(c) for c in r] for r in rows]})
    note = f"read {len(read)} file(s)" + (f"; not read (PDF, slides, images or unreadable): {len(skipped)}" if skipped else "")
    return out, note
