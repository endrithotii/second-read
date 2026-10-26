"""Names to standard identifiers, checked by the services that own them.

gene     -> HGNC symbol and id (rest.genenames.org), aliases resolved (HER2 -> ERBB2)
variant  -> HGVS on the gene's MANE Select transcript, checked by VariantValidator.
            Protein changes ("C797S") become NP_...:p.(Cys797Ser); VariantValidator
            confirms the reference amino acid is right at that position.
            Exon-level and copy-number events (exon 19 deletion, amplification,
            fusion) have no single HGVS form; they get a controlled category instead.
drug     -> ChEMBL id by exact synonym (AZD9291 and Tagrisso both -> CHEMBL3353410)
disease  -> OncoTree code from a small table of the lung cancer types this topic
            needs, checked against the OncoTree API when the table is built.

Every lookup is cached on disk, so a run is repeatable offline and cheap.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "second-read/0.2 (+https://github.com/endrithotii/second-read)"
AA3 = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "Q": "Gln", "E": "Glu", "G": "Gly", "H": "His",
       "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe", "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp",
       "Y": "Tyr", "V": "Val", "*": "Ter"}
AA1 = {v.upper(): k for k, v in AA3.items() if k != "X"}

# Disease names a paper uses -> OncoTree code. Kept small on purpose: this
# topic is EGFR-mutant non-small cell lung cancer. Anything else is "unmapped".
ONCOTREE = {
    "non-small cell lung cancer": "NSCLC", "non small cell lung cancer": "NSCLC", "nsclc": "NSCLC",
    "lung non-small cell carcinoma": "NSCLC",
    "lung adenocarcinoma": "LUAD", "adenocarcinoma of the lung": "LUAD", "luad": "LUAD",
    "lung squamous cell carcinoma": "LUSC", "squamous cell lung cancer": "LUSC",
    "small cell lung cancer": "SCLC", "small cell lung carcinoma": "SCLC", "sclc": "SCLC",
    "lung cancer": "LUNG", "lung carcinoma": "LUNG", "lung": "LUNG",
    "large cell neuroendocrine carcinoma": "LCNEC",
    "high grade glioma": "HGGNOS", "glioblastoma": "GB",
}
# Parent codes, so that a paper saying "lung adenocarcinoma" and a curator
# saying "non-small cell lung cancer" are recognised as the same branch.
ONCOTREE_PARENT = {"LUAD": "NSCLC", "LUSC": "NSCLC", "LCNEC": "NSCLC", "NSCLC": "LUNG", "SCLC": "LUNG"}

CATEGORIES = [
    (r"\bexon\s*19\b.*\b(del|deletion)", "Exon 19 Deletion"),
    (r"\b(del|deletion)s?\b.*\bexon\s*19\b", "Exon 19 Deletion"),
    (r"\bexon\s*20\b.*\b(ins|insertion)", "Exon 20 Insertion"),
    (r"\b(ins|insertion)s?\b.*\bexon\s*20\b", "Exon 20 Insertion"),
    (r"\bamplifi", "Amplification"), (r"\bcopy[- ]number gain\b", "Amplification"),
    (r"\bfusion|rearrange", "Fusion"), (r"\boverexpress|\bexpression\b", "Expression"),
    (r"\bloss\b|\bdeletion\b", "Loss"), (r"\bmutation|mutant|mutated\b", "Mutation"),
    (r"\bwild[- ]?type\b", "Wildtype"),
]
INDEL = re.compile(r"^(?:p\.\(?)?([A-Z])(\d{1,4})(?:_([A-Z])(\d{1,4}))?(del|dup|ins|delins)([A-Z]*)\)?$")
# "G719X" or "G719": any change at that residue. In this shorthand X means
# "any amino acid", not a stop codon.
CODON = re.compile(r"^([A-Z])(\d{2,4})X?$")
SUBST = re.compile(r"^(?:p\.\(?)?([A-Z][a-z]{2}|[A-Z])(\d{1,4})([A-Z][a-z]{2}|[A-Z*])\)?$")


class Lookups:
    def __init__(self, cache: Path):
        self.path = cache / "lookups.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=0, sort_keys=True), encoding="utf-8")

    def _cached(self, kind: str, key: str, fetch):
        k = f"{kind}:{key}"
        if k not in self.data:
            self.data[k] = fetch()
            self.save()
        return self.data[k]

    def _json(self, url: str, accept_json_header: bool = False) -> dict | list:
        headers = {"user-agent": UA}
        if accept_json_header:
            headers["accept"] = "application/json"
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                    return json.load(r)
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    # ---- gene ----
    def gene(self, name: str) -> dict | None:
        name = (name or "").strip().upper()
        if not name:
            return None

        def fetch():
            for field in ("symbol", "alias_symbol", "prev_symbol"):
                d = self._json(f"https://rest.genenames.org/fetch/{field}/{urllib.parse.quote(name)}", True)
                docs = d["response"]["docs"]
                if len(docs) == 1:
                    x = docs[0]
                    mane = [t for t in x.get("mane_select", []) if t.startswith("NM_")]
                    return {"symbol": x["symbol"], "hgnc_id": x["hgnc_id"], "mane_nm": mane[0] if mane else None}
            return None
        return self._cached("gene", name, fetch)

    def protein_accession(self, nm: str) -> str | None:
        def fetch():
            gene_tx = self._json(f"https://rest.variantvalidator.org/VariantValidator/tools/gene2transcripts_v2/"
                                 f"{urllib.parse.quote(nm)}/mane_select/refseq/GRCh38?content-type=application/json")
            items = gene_tx if isinstance(gene_tx, list) else [gene_tx]
            for g in items:
                for t in g.get("transcripts", []):
                    if t.get("reference") == nm:
                        return t.get("translation")
            return None
        return self._cached("np", nm, fetch)

    # ---- variant ----
    def validate(self, hgvs: str) -> dict:
        """Ask VariantValidator. Returns {"valid": bool, "hgvs_p": ..., "hgvs_c": ..., "note": ...}."""
        def fetch():
            url = (f"https://rest.variantvalidator.org/VariantValidator/variantvalidator/GRCh38/"
                   f"{urllib.parse.quote(hgvs, safe=':()')}/mane_select?content-type=application/json")
            d = self._json(url)
            for k, v in d.items():
                if k in ("flag", "metadata") or not isinstance(v, dict):
                    continue
                warnings = v.get("validation_warnings") or []
                text = " ".join(warnings)
                if "valid reference amino acid" in text:
                    return {"valid": True, "hgvs_p": hgvs, "hgvs_c": None, "note": "reference amino acid confirmed"}
                if v.get("hgvs_transcript_variant") and not any("Error" in w or "does not agree" in w for w in warnings):
                    return {"valid": True, "hgvs_c": v["hgvs_transcript_variant"],
                            "hgvs_p": (v.get("hgvs_predicted_protein_consequence") or {}).get("tlr"), "note": ""}
                return {"valid": False, "hgvs_p": None, "hgvs_c": None, "note": "; ".join(warnings)[:300]}
            return {"valid": False, "hgvs_p": None, "hgvs_c": None, "note": "no answer"}
        out = self._cached("vv", hgvs, fetch)
        time.sleep(0.0)
        return out

    def variant(self, gene: str | None, text: str) -> dict:
        """One variant mention -> normalised form."""
        raw = (text or "").strip()
        g = self.gene(gene) if gene else None
        # Strip a leading gene name: "EGFR C797S" -> "C797S".
        rest = raw
        if g and rest.upper().startswith(g["symbol"]):
            rest = rest[len(g["symbol"]):].strip()
        if gene and rest.upper().startswith(gene.upper()):
            rest = rest[len(gene):].strip()
        m = CODON.match(rest.replace(" ", ""))
        if m and g:
            return {"gene": g["symbol"], "kind": "codon", "name": f"{m.group(1)}{m.group(2)}", "hgvs": None,
                    "validated": None, "note": "codon-level description (any change at this residue)"}
        m = SUBST.match(rest.replace(" ", ""))
        if m and g and g.get("mane_nm"):
            ref, pos, alt = m.groups()
            ref1 = ref if len(ref) == 1 else AA1.get(ref.upper())
            alt1 = alt if len(alt) == 1 else AA1.get(alt.upper(), alt)
            if ref1 and alt1 and ref1 in AA3 and alt1 in AA3:
                np_ = self.protein_accession(g["mane_nm"])
                hgvs = f"{np_}:p.({AA3[ref1]}{pos}{AA3[alt1]})" if np_ else None
                check = self.validate(hgvs) if hgvs else {"valid": False, "note": "no protein reference"}
                return {"gene": g["symbol"], "kind": "protein change", "name": f"{ref1}{pos}{alt1}",
                        "hgvs": hgvs if check["valid"] else None, "validated": check["valid"], "note": check["note"]}
        m = INDEL.match(rest.replace(" ", ""))
        if m and g and g.get("mane_nm"):
            r1, p1, r2, p2, op, ins = m.groups()
            name = f"{r1}{p1}" + (f"_{r2}{p2}" if r2 else "") + op + ins
            np_ = self.protein_accession(g["mane_nm"])
            # HGVS writes duplications without the duplicated residues ("dupASV" -> "dup").
            tail = "" if op in ("del", "dup") else "".join(AA3.get(a, a) for a in ins)
            three = f"{AA3[r1]}{p1}" + (f"_{AA3.get(r2, r2)}{p2}" if r2 else "") + op + tail
            hgvs = f"{np_}:p.({three})" if np_ else None
            check = self.validate(hgvs) if hgvs else {"valid": False, "note": "no protein reference"}
            return {"gene": g["symbol"], "kind": "protein indel", "name": name,
                    "hgvs": hgvs if check["valid"] else None, "validated": check["valid"], "note": check["note"]}
        if re.match(r"^(NM_|NC_|NP_|ENST)", rest):
            check = self.validate(rest)
            return {"gene": g["symbol"] if g else gene, "kind": "HGVS as written", "name": rest,
                    "hgvs": check.get("hgvs_p") or check.get("hgvs_c"), "validated": check["valid"], "note": check["note"]}
        low = raw.lower()
        for pattern, cat in CATEGORIES:
            if re.search(pattern, low):
                return {"gene": g["symbol"] if g else gene, "kind": "category", "name": cat, "hgvs": None,
                        "validated": None, "note": "no single HGVS form for this kind of alteration"}
        return {"gene": g["symbol"] if g else gene, "kind": "unparsed", "name": raw, "hgvs": None,
                "validated": False, "note": "not recognised as a protein change, HGVS or known category"}

    # ---- drug ----
    def drug(self, name: str) -> dict | None:
        name = (name or "").strip()
        if not name:
            return None

        def fetch():
            q = urllib.parse.quote(name)
            d = self._json(f"https://www.ebi.ac.uk/chembl/api/data/molecule.json?molecule_synonyms__molecule_synonym__iexact={q}"
                           f"&only=molecule_chembl_id,pref_name,max_phase")
            mols = d.get("molecules", [])
            if not mols:
                d = self._json(f"https://www.ebi.ac.uk/chembl/api/data/molecule.json?pref_name__iexact={q}"
                               f"&only=molecule_chembl_id,pref_name,max_phase")
                mols = d.get("molecules", [])
            # Prefer the parent drug over salts ("OSIMERTINIB" over "OSIMERTINIB MESYLATE").
            mols.sort(key=lambda m: len(m.get("pref_name") or "zzzzzzzzzzzzzzzzzzzz"))
            return {"chembl_id": mols[0]["molecule_chembl_id"], "name": mols[0]["pref_name"]} if mols else None
        return self._cached("drug", name.lower(), fetch)

    # ---- disease ----
    @staticmethod
    def disease(name: str) -> str | None:
        low = re.sub(r"\s+", " ", (name or "").lower()).strip()
        low = re.sub(r"^(egfr[- ]mutant|egfr[- ]mutated|advanced|metastatic|stage [iv]+)\s+", "", low)
        for key in sorted(ONCOTREE, key=len, reverse=True):
            if key in low:
                return ONCOTREE[key]
        return None


def same_disease_branch(a: str | None, b: str | None) -> bool:
    """True if one OncoTree code is the other or an ancestor of it."""
    if not a or not b:
        return False

    def chain(c):
        out = [c]
        while c in ONCOTREE_PARENT:
            c = ONCOTREE_PARENT[c]
            out.append(c)
        return out
    return a in chain(b) or b in chain(a)
