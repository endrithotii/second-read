"""Offline stand-in for the language models, for tests and layout previews only.

It answers with crude keyword rules so the pipeline and report can be
exercised without API keys. Its output is not a real screening or
extraction, and any report made with it carries a banner saying so.
"""

import json
import re

from litwatch.stages import CHECK_SYSTEM, EXTRACT_SYSTEM, SCREEN_SYSTEM, SUMMARY_SYSTEM

_LINE = re.compile(r"^\[(\d+)\] (.*)$", re.M)

RULES = {
    "population": r"\b(EGFR|NSCLC|lung)",
    "drug": r"(osimertinib|AZD9291|Tagrisso)",
    "resistance": r"resistan",
    "primary_data": r"\b(patients|cells|cohort|trial|mice|xenograft|case)\b",
}
FIELD_RULES = {
    "variant": r"\b(C797S|MET amplification|T790M|HER2 amplification|L718Q|G724S|EGFR [A-Z]\d+[A-Z])\b",
    "drug": r"\b(osimertinib(?: plus [a-z\- ]+)?|amivantamab|savolitinib|lazertinib)\b",
    "study_type": r"\b(retrospective(?: cohort)?|prospective|phase (?:1|2|3|I|II|III)|case report|cell lines?|xenograft)\b",
    "n_patients": r"\b(\d{2,4}) patients\b",
}


def _sentences(user: str) -> dict:
    return {int(i): s for i, s in _LINE.findall(user)}


class StubModel:
    def __init__(self, name: str):
        self.name = f"offline-stub/{name}"

    def complete_json(self, system: str, user: str) -> dict:
        sents = _sentences(user)
        if system == SCREEN_SYSTEM:
            out = []
            for cid, pat in RULES.items():
                hit = next((i for i, s in sents.items() if re.search(pat, s, re.I)), None)
                out.append({"id": cid, "answer": "yes" if hit is not None else "no", "sentence": hit})
            return {"criteria": out}
        if system == EXTRACT_SYSTEM:
            fields = {}
            for fid, pat in FIELD_RULES.items():
                for i, s in sents.items():
                    m = re.search(pat, s, re.I)
                    if m:
                        fields[fid] = {"value": m.group(1), "sentence": i, "quote": m.group(0)}
                        break
            text = " ".join(sents.values())
            if re.search(r"mechanism", text, re.I):
                ft = "resistance mechanism"
            elif re.search(r"after (progression|resistance)", text, re.I):
                ft = "treatment after resistance"
            else:
                ft = "other"
            fields["finding_type"] = {"value": ft, "sentence": None, "quote": None}
            concl = next((s for i, s in sorted(sents.items()) if s.lower().startswith("conclusion")), None)
            if concl:
                i = next(k for k, v in sents.items() if v == concl)
                body = concl.split(":", 1)[-1].strip()
                fields["key_finding"] = {"value": body, "sentence": i, "quote": body}
            return {"fields": fields}
        if system == SUMMARY_SYSTEM:
            facts = json.loads(user.split("\n", 1)[1])
            first = f"This {facts.get('study_type', 'study')} concerns {facts.get('drug', 'osimertinib')}"
            if facts.get("variant"):
                first += f" and {facts['variant']}"
            second = facts.get("key_finding") or "The abstract reports results on resistance."
            return {"summary": f"{first}. {second}"}
        if system == CHECK_SYSTEM:
            claims = re.findall(r"^(\d+)\. (.*)$", user.split("Claims:", 1)[1], re.M)
            verdicts = []
            for num, claim in claims:
                value = claim.split(": ", 1)[-1]
                if claim.startswith("finding_type"):
                    key = value.split()[-1]
                    hit = next((i for i, s in sents.items() if key.lower() in s.lower()), None)
                    verdicts.append({"claim": int(num), "verdict": "supported" if hit else "beyond",
                                     "sentence": hit})
                    continue
                words = [w for w in re.findall(r"[A-Za-z0-9]{4,}", value)]
                hit = next((i for i, s in sents.items()
                            if words and sum(w.lower() in s.lower() for w in words) >= max(1, len(words) // 2)), None)
                verdicts.append({"claim": int(num), "verdict": "supported" if hit is not None else "beyond",
                                 "sentence": hit})
            return {"verdicts": verdicts, "omitted": []}
        raise ValueError("unknown prompt")
