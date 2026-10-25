import unittest

from litwatch.llm import parse_json
from litwatch.misses import audit_sample, capture_recapture
from litwatch.pubmed import Paper
from litwatch.stages import code_checks, route
from litwatch.textutil import normalise, numbers_in, split_sentences

TOPIC = {
    "synonyms": {"osimertinib": ["AZD9291"]},
    "high_impact": {"field": "finding_type", "values": ["resistance mechanism"]},
}


def paper(abstract: str, title: str = "A study") -> Paper:
    return Paper(pmid="1", title=title, abstract=abstract, sentences=split_sentences(abstract))


def extraction(**fields) -> dict:
    return {"fields": {k: {"value": v[0], "sentence": 1, "quote": v[1]} for k, v in fields.items()}}


class TextTests(unittest.TestCase):
    def test_sentences_keep_abbreviations(self):
        s = split_sentences("Resistance arose, e.g. C797S in 12 patients. MET was amplified.")
        self.assertEqual(len(s), 2)

    def test_numbers_and_dashes(self):
        self.assertEqual(numbers_in("41% vs 14,5 %"), ["41", "14.5"])
        self.assertEqual(normalise("p.T790M – cis"), "p.T790M - cis")


class CodeCheckTests(unittest.TestCase):
    ABSTRACT = "EGFR C797S was found in 12 of 45 patients after osimertinib. It occurred in cis with T790M."

    def test_grounded_value_passes(self):
        ex = extraction(variant=("EGFR C797S", "EGFR C797S was found"), n_patients=("45", "12 of 45 patients"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": "C797S occurred in 45 patients."}, TOPIC)
        self.assertTrue(all(f["passed"] for f in res["fields"].values()))
        self.assertTrue(res["summary"]["passed"])

    def test_wrong_number_fails(self):
        ex = extraction(n_patients=("54", "12 of 45 patients"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": ""}, TOPIC)
        self.assertFalse(res["fields"]["n_patients"]["passed"])

    def test_invented_quote_fails(self):
        ex = extraction(variant=("L718Q", "L718Q was found"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": ""}, TOPIC)
        self.assertIn("word for word", res["fields"]["variant"]["problems"][0])

    def test_summary_number_must_come_from_record(self):
        ex = extraction(n_patients=("45", "12 of 45 patients"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": "Seen in 60 patients."}, TOPIC)
        self.assertFalse(res["summary"]["passed"])

    def test_synonym_counts(self):
        ab = "AZD9291 resistance was studied."
        ex = extraction(drug=("osimertinib", "AZD9291 resistance"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertTrue(res["fields"]["drug"]["passed"])


class RobustnessTests(unittest.TestCase):
    """Failures seen with a small local model on a real abstract."""

    ABSTRACT = ("We identified SAA1 as a secreted factor that increases during resistance in patients "
                "receiving osimertinib. Organoids and xenografts were treated.")

    def test_quote_that_does_not_say_the_value_fails(self):
        ex = extraction(study_type=("retrospective cohort",
                                    "We identified SAA1 as a secreted factor that increases during resistance"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": ""}, TOPIC)
        self.assertFalse(res["fields"]["study_type"]["passed"])

    def test_half_supported_value_fails(self):
        ex = extraction(setting=("first-line osimertinib", "patients receiving osimertinib"))
        res = code_checks(paper(self.ABSTRACT), ex, {"text": ""}, TOPIC)
        self.assertFalse(res["fields"]["setting"]["passed"])

    def test_abbreviation_matches_spelled_out(self):
        ab = "Acquired resistance to third-generation tyrosine kinase inhibitors was studied."
        ex = extraction(setting=("acquired resistance to third-generation TKIs",
                                 "acquired resistance to third-generation tyrosine kinase inhibitors"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertTrue(res["fields"]["setting"]["passed"], res["fields"]["setting"])

    def test_quote_with_ellipsis(self):
        ab = "The patient received T-DXd for 7 cycles, achieving a partial response with regression of lesions."
        ex = extraction(key_finding=("partial response with regression of lesions",
                                     "The patient received T-DXd ... achieving a partial response with regression of lesions"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertTrue(res["fields"]["key_finding"]["passed"], res["fields"]["key_finding"])

    def test_number_words(self):
        ab = "Thirty-one patients received study treatment in a two-patient pilot."
        ex = extraction(n_patients=("31", "Thirty-one patients received study treatment"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertTrue(res["fields"]["n_patients"]["passed"], res["fields"]["n_patients"])

    def test_papers_own_abbreviations_and_word_forms(self):
        ab = ("We evaluated pirfenidone (PFD) with osimertinib (OS). "
              "PFD in combination with OS was synergistic. RRBP1 was upregulated in resistant cells.")
        ex = extraction(drug=("pirfenidone combined with osimertinib", "PFD in combination with OS"),
                        mechanism=("RRBP1 upregulation", "RRBP1 was upregulated"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertTrue(res["fields"]["drug"]["passed"], res["fields"]["drug"])
        self.assertTrue(res["fields"]["mechanism"]["passed"], res["fields"]["mechanism"])

    def test_judgement_field_skips_word_check(self):
        topic = dict(TOPIC, fields=[{"id": "study_type", "kind": "judgement"}])
        ab = "EGFR-mutant cell lines were treated with osimertinib."
        ex = extraction(study_type=("cell-line study", "EGFR-mutant cell lines were treated"))
        res = code_checks(paper(ab), ex, {"text": ""}, topic)
        self.assertTrue(res["fields"]["study_type"]["passed"])

    def test_unsupported_addition_still_fails(self):
        ab = "Osimertinib was given as fifth-line therapy."
        ex = extraction(setting=("fifth-line therapy after progression following multiple prior lines",
                                 "as fifth-line therapy"))
        res = code_checks(paper(ab), ex, {"text": ""}, TOPIC)
        self.assertFalse(res["fields"]["setting"]["passed"])

    def test_sentence_numbers_as_text_and_null_strings(self):
        from litwatch.stages import as_index, clean_value
        self.assertEqual([as_index("5"), as_index("[5]"), as_index("S5"), as_index(None)], [5, 5, 5, None])
        self.assertIsNone(clean_value("null"))
        self.assertIsNone(clean_value("Not stated."))
        self.assertEqual(clean_value(45), "45")


class CategoryTests(unittest.TestCase):
    TOPIC = dict(TOPIC, fields=[{"id": "finding_type", "kind": "category",
                                 "values": ["resistance mechanism", "other"]}])

    def test_category_needs_no_quote(self):
        ex = {"fields": {"finding_type": {"value": "Resistance mechanism", "sentence": 1, "quote": None}}}
        res = code_checks(paper("Text."), ex, {"text": ""}, self.TOPIC)
        self.assertTrue(res["fields"]["finding_type"]["passed"])

    def test_category_must_be_allowed(self):
        ex = {"fields": {"finding_type": {"value": "biomarker", "sentence": 1, "quote": None}}}
        res = code_checks(paper("Text."), ex, {"text": ""}, self.TOPIC)
        self.assertFalse(res["fields"]["finding_type"]["passed"])


class RouteTests(unittest.TestCase):
    def test_high_impact_goes_to_review_even_when_clean(self):
        scr = {"decision": "include", "criteria": [], "reason": ""}
        checks = {"fields": {}, "summary": {"passed": True, "problems": []}}
        verdicts = {"claims": [], "omitted": []}
        ex = extraction(finding_type=("resistance mechanism", None))
        r = route(scr, checks, verdicts, ex, TOPIC)
        self.assertEqual(r["outcome"], "review")
        self.assertEqual(r["problems"], 0)

    def test_contradiction_is_a_problem(self):
        scr = {"decision": "include", "criteria": [], "reason": ""}
        checks = {"fields": {}, "summary": {"passed": True, "problems": []}}
        verdicts = {"claims": [{"id": "n_patients", "kind": "field", "verdict": "contradicted"}], "omitted": []}
        r = route(scr, checks, verdicts, extraction(), TOPIC)
        self.assertEqual((r["outcome"], r["problems"]), ("review", 1))


class MissesTests(unittest.TestCase):
    def test_slide_example(self):
        # The worked example from the slides: 40 and 30 found, 24 shared.
        a = {str(i) for i in range(40)}
        b = {str(i) for i in range(16, 46)}
        cr = capture_recapture(a, b)
        self.assertEqual(cr["found"], 46)
        # Chapman: 41 * 31 / 25 - 1 = 49.84. The plain n1 * n2 / m on the slide gives 50.
        self.assertAlmostEqual(cr["estimate"], 49.8, places=1)
        self.assertEqual(cr["missed"], 4)

    def test_sample_is_reproducible(self):
        ids = [str(i) for i in range(20)]
        self.assertEqual(audit_sample(ids, 5, "x"), audit_sample(ids, 5, "x"))


class ProviderTests(unittest.TestCase):
    def test_local_model_needs_no_key(self):
        from litwatch.llm import make_model
        m = make_model("ollama:llama3.1:8b")
        self.assertEqual(m.name, "ollama/llama3.1:8b")
        self.assertTrue(m.url.endswith("/v1/chat/completions"))

    def test_family_is_the_model_maker_not_the_host(self):
        from litwatch.llm import family
        self.assertEqual(family("groq:openai/gpt-oss-120b"), "openai")
        self.assertEqual(family("groq:qwen/qwen3.8-27b"), "qwen")
        self.assertEqual(family("ollama:llama3.1:8b"), "meta")
        self.assertNotEqual(family("groq:openai/gpt-oss-120b"), family("groq:qwen/qwen3.8-27b"))

    def test_unknown_provider(self):
        from litwatch.llm import make_model
        with self.assertRaises(ValueError):
            make_model("nope:x")


class CacheTests(unittest.TestCase):
    def test_new_checker_reruns_only_the_check(self):
        import tempfile
        from pathlib import Path
        from litwatch.run import Cache, process
        sys_path_topic = {"question": "q", "criteria": [{"id": "drug", "required": True, "text": "t"}],
                          "fields": [{"id": "drug", "text": "t"}], "synonyms": {}}
        p = paper("Osimertinib was studied in patients.")

        class Fake:
            def __init__(self, name):
                self.name, self.calls = name, []

            def complete_json(self, system, user):
                self.calls.append(system[:20])
                if "screen" in system:
                    return {"criteria": [{"id": "drug", "answer": "yes", "sentence": 1}]}
                if "extract" in system:
                    return {"fields": {"drug": {"value": "osimertinib", "sentence": 1, "quote": "Osimertinib"}}}
                if "two-sentence" in system:
                    return {"summary": "Osimertinib was studied."}
                return {"verdicts": [{"claim": 1, "verdict": "supported", "sentence": 1},
                                     {"claim": 2, "verdict": "supported", "sentence": 1}], "omitted": []}

        writer, checker1, checker2 = Fake("w"), Fake("c1"), Fake("c2")
        with tempfile.TemporaryDirectory() as d:
            cache = Cache(Path(d), sys_path_topic, writer)
            first = process(p, sys_path_topic, writer, checker1)
            cache.put(p, first)
            writer_calls = len(writer.calls)
            second = process(p, sys_path_topic, writer, checker2, cached=cache.get(p))
        self.assertEqual(len(writer.calls), writer_calls)  # writer not called again
        self.assertEqual(len(checker2.calls), 1)          # only the checker ran
        self.assertEqual(second["route"]["outcome"], "accepted")


class ParseTests(unittest.TestCase):
    def test_fenced_json(self):
        self.assertEqual(parse_json('Here:\n```json\n{"a": 1}\n```'), {"a": 1})


if __name__ == "__main__":
    unittest.main()
