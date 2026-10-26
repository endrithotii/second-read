"""Tests for knowledge-base mode. No network: lookups are stubbed."""

import unittest

from litwatch.kb import compare, entries
from litwatch.kb.normalise import Lookups, same_disease_branch
from litwatch.kb.sources import Source, parse_jats
from litwatch.kb.stats import kappa, wilson, wss_at


class StubLookups(Lookups):
    def __init__(self):
        self.data = {}

    def gene(self, name):
        return {"symbol": {"HER2": "ERBB2"}.get(name.upper(), name.upper()), "hgnc_id": "HGNC:0", "mane_nm": "NM_1"}

    def protein_accession(self, nm):
        return "NP_1"

    def validate(self, hgvs):
        bad = "Ala797" in hgvs
        return {"valid": not bad, "hgvs_p": hgvs, "hgvs_c": None, "note": "reference mismatch" if bad else "ok"}

    def drug(self, name):
        known = {"osimertinib": "OSIMERTINIB", "azd9291": "OSIMERTINIB", "erlotinib": "ERLOTINIB"}
        n = known.get(name.lower())
        return {"chembl_id": "CHEMBL1", "name": n} if n else None

    def save(self):
        pass


JATS = b"""<article><front/><body>
<sec><title>Results</title>
<p>Cells with C797S were resistant to osimertinib<xref ref-type="bibr">12</xref> (Figure 2A). Growth continued.</p>
<table-wrap><label>Table 1</label><caption><p>Mutations</p></caption>
<table><tr><th>Variant</th><th>Patients</th></tr><tr><td>C797S</td><td>7</td></tr></table></table-wrap>
</sec></body></article>"""


class Sources(unittest.TestCase):
    def test_jats_sentences_tables_and_citations(self):
        src = parse_jats(JATS, Source(pmid="1", abstract=["Abstract sentence."]))
        self.assertEqual(src.text_source, "full text (PMC JATS)")
        self.assertEqual(src.body[0]["text"], "Cells with C797S were resistant to osimertinib (Figure 2A).")
        self.assertEqual(src.tables[0]["rows"], [["Variant", "Patients"], ["C797S", "7"]])
        units = src.units()
        self.assertEqual(units["T1.r2.c1"], "C797S")
        self.assertEqual(units["A1"], "Abstract sentence.")

    def test_retraction_is_seen(self):
        self.assertTrue(Source(pmid="1", pub_types=["Retracted Publication"]).retracted)
        self.assertTrue(Source(pmid="1", corrections=[{"type": "retraction"}]).retracted)
        self.assertFalse(Source(pmid="1", corrections=[{"type": "erratum"}]).retracted)


class Normalise(unittest.TestCase):
    def setUp(self):
        self.lk = StubLookups()

    def test_protein_change_and_reference_check(self):
        v = self.lk.variant("EGFR", "EGFR C797S")
        self.assertEqual((v["name"], v["hgvs"], v["validated"]), ("C797S", "NP_1:p.(Cys797Ser)", True))
        bad = self.lk.variant("EGFR", "A797S")
        self.assertFalse(bad["validated"])

    def test_indel_codon_and_categories(self):
        self.assertEqual(self.lk.variant("EGFR", "E746_A750del")["hgvs"], "NP_1:p.(Glu746_Ala750del)")
        self.assertEqual(self.lk.variant("EGFR", "A767_V769dupASV")["hgvs"], "NP_1:p.(Ala767_Val769dup)")
        self.assertEqual(self.lk.variant("EGFR", "G719X")["kind"], "codon")
        self.assertEqual(self.lk.variant("EGFR", "exon 19 deletion")["name"], "Exon 19 Deletion")
        self.assertEqual(self.lk.variant("HER2", "amplification")["gene"], "ERBB2")

    def test_disease_branch(self):
        self.assertEqual(Lookups.disease("EGFR-mutant non-small cell lung cancer"), "NSCLC")
        self.assertEqual(Lookups.disease("advanced lung adenocarcinoma"), "LUAD")
        self.assertTrue(same_disease_branch("LUAD", "NSCLC"))
        self.assertFalse(same_disease_branch("SCLC", "NSCLC"))


class Checks(unittest.TestCase):
    def setUp(self):
        self.src = parse_jats(JATS, Source(pmid="1", abstract=["Abstract sentence."]))
        self.lk = StubLookups()
        self.good = {"variants": [{"gene": "EGFR", "variant": "C797S"}], "disease": "lung adenocarcinoma",
                     "drugs": ["osimertinib"], "evidence_type": "PREDICTIVE", "direction": "SUPPORTS",
                     "significance": "RESISTANCE", "design": "cell line or biochemical", "n_patients": None,
                     "units": ["S1"], "quote": "Cells with C797S were resistant to osimertinib."}

    def test_good_entry_passes_code_checks(self):
        c = entries.check_entry(self.good, self.src, {}, self.lk)
        self.assertEqual(c["code_problems"], [])
        self.assertEqual(c["level"], "D")  # decided by code from the design

    def test_invented_quote_drug_and_count_are_caught(self):
        bad = dict(self.good, quote="Cells with C797S were sensitive to osimertinib", drugs=["erlotinib"], n_patients=12)
        problems = " | ".join(entries.check_entry(bad, self.src, {}, self.lk)["code_problems"])
        self.assertIn("quote is not in the cited units", problems)
        self.assertIn("drug 'erlotinib' is not named", problems)
        self.assertIn("patient count 12", problems)

    def test_routing_needs_agreement(self):
        c = entries.check_entry(self.good, self.src, {}, self.lk)
        c["checker"] = {"supported": "yes", "evidence_type": "PREDICTIVE", "direction": "SUPPORTS",
                        "significance": "RESISTANCE", "design": "cell line or biochemical"}
        entries.route(c)
        self.assertEqual(c["route"], "accepted")
        c["checker"]["significance"] = "SENSITIVITYRESPONSE"
        entries.route(c)
        self.assertEqual(c["route"], "to a person")


class Compare(unittest.TestCase):
    def setUp(self):
        self.lk = StubLookups()
        self.entry = {"variants": [{"gene": "EGFR", "name": "C797S"}], "drugs": [{"name": "OSIMERTINIB"}],
                      "oncotree": "LUAD", "significance": "RESISTANCE", "direction": "SUPPORTS", "level": "D",
                      "evidence_type": "PREDICTIVE"}

    def item(self, **kw):
        base = {"eid": 1, "pmid": "9", "variants": [{"gene": "EGFR", "name": "C797S"}], "therapies": ["Osimertinib"],
                "disease": "Lung Adenocarcinoma", "significance": "RESISTANCE", "direction": "SUPPORTS", "level": "D",
                "type": "PREDICTIVE", "submitted": "2020-01-01"}
        base.update(kw)
        return base

    def test_labels(self):
        self.assertEqual(compare.label(self.entry, [], self.lk)[0], "new")
        self.assertEqual(compare.label(self.entry, [self.item()], self.lk)[0], "supports")
        self.assertEqual(compare.label(self.entry, [self.item(significance="SENSITIVITYRESPONSE")], self.lk)[0], "contradicts")
        self.assertEqual(compare.label(self.entry, [self.item(level="B")], self.lk)[0], "refines")

    def test_a_paper_cannot_confirm_itself(self):
        self.assertEqual(compare.label(self.entry, [self.item(pmid="5")], self.lk, exclude_pmid="5")[0], "new")

    def test_or_profiles_match_any_member(self):
        it = self.item(profile="EGFR C797S OR EGFR L718Q",
                       variants=[{"gene": "EGFR", "name": "C797S"}, {"gene": "EGFR", "name": "L718Q"}])
        self.assertEqual(compare.match([self.entry], [it])[0][0], self.entry)
        both = self.item(profile="EGFR C797S AND EGFR L718Q",
                         variants=[{"gene": "EGFR", "name": "C797S"}, {"gene": "EGFR", "name": "L718Q"}])
        self.assertIsNone(compare.match([self.entry], [both])[0][0])

    def test_kb_as_of_date(self):
        self.assertEqual(len(compare.kb_as_of([self.item(submitted="2026-01-01")], "2025-10-26")), 0)

    def test_salt_names_match_parent_drug(self):
        e = dict(self.entry, drugs=[{"name": "OSIMERTINIB MESYLATE"}])
        self.assertEqual(compare.match([e], [self.item()])[0][0], e)


class Stats(unittest.TestCase):
    def test_wilson_known_value(self):
        p, lo, hi = wilson(8, 10)
        self.assertAlmostEqual(lo, 0.4902, places=3)
        self.assertAlmostEqual(hi, 0.9433, places=3)

    def test_kappa(self):
        self.assertAlmostEqual(kappa(["a", "a", "b", "b"], ["a", "a", "b", "b"]), 1.0)
        self.assertAlmostEqual(kappa(["a", "b", "a", "b"], ["b", "a", "b", "a"]), -1.0)

    def test_wss(self):
        # Perfect ranking of 2 relevant among 10: read 2, skip 8 -> 0.8 - 0.05.
        ranked = [(10, True, 1), (9, True, 1)] + [(1, False, 1)] * 8
        self.assertAlmostEqual(wss_at(ranked), 0.75)
        # Ties cannot be split by a threshold.
        tied = [(5, True, 1), (5, False, 1), (5, True, 1), (5, False, 1)]
        self.assertAlmostEqual(wss_at(tied), -0.05)


if __name__ == "__main__":
    unittest.main()
