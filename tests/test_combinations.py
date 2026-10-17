from pathlib import Path
import tempfile
import unittest
from candidatelab.combinations import CombinationSettings, combinations
from candidatelab.config import Config
from candidatelab.engine import run
from candidatelab.transforms import TransformSettings, variants

class CombinationTests(unittest.TestCase):
    def test_default_disabled(self):
        self.assertEqual(list(combinations(["red", "moon"], CombinationSettings())), [])

    def test_explicit_order_separators_and_no_reuse(self):
        settings = CombinationSettings(enabled=True, separators=["", "-"])
        self.assertEqual(list(combinations(["red", "moon"], settings)), ["redmoon", "red-moon", "moonred", "moon-red"])

    def test_three_tokens_opt_in_and_raw_cap(self):
        settings = CombinationSettings(enabled=True, max_tokens=3, max_combinations=7)
        self.assertEqual(list(combinations(["a", "b", "c"], settings)), ["ab", "ac", "ba", "bc", "ca", "cb", "abc"])

    def test_token_reuse(self):
        self.assertEqual(list(combinations(["a"], CombinationSettings(enabled=True))), [])
        self.assertEqual(list(combinations(["a"], CombinationSettings(enabled=True, allow_reuse=True))), ["aa"])

    def test_duplicate_terms_do_not_imply_reuse(self):
        self.assertEqual(list(combinations(["a", "a"], CombinationSettings(enabled=True))), [])

    def test_explicit_numeric_and_date_formats(self):
        settings = CombinationSettings(enabled=True, numeric_tokens=["42"], dates=["2001-02-03"], date_formats=["%d%m"])
        self.assertEqual(list(combinations(["moon"], settings)), ["moon42", "moon0302", "42moon", "420302", "0302moon", "030242"])

    def test_validation(self):
        for data in ({"dates":["2001-02-03"]}, {"dates":["2001-02-30"],"date_formats":["%Y"]}, {"numeric_tokens":["abc"]}, {"max_tokens":4}, {"enabled":1}, {"date_formats":["%c"]}, {"separators":[]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                CombinationSettings.from_dict(data)
        with self.assertRaisesRegex(ValueError,"catalog"):
            Config(seeds=[str(i) for i in range(256)], combinations={"numeric_tokens":["9999"]})

    def test_lazy_large_catalog_tiny_cap(self):
        settings = CombinationSettings(enabled=True, max_tokens=3, max_combinations=1)
        self.assertEqual(len(list(combinations([str(x) for x in range(256)], settings))), 1)

    def test_normalization_retains_original(self):
        self.assertEqual(list(variants("e\u0301", TransformSettings(normalizations=["NFC"]))), ["e\u0301", "é"])

    def test_engine_combinations_then_mutations_with_attempt_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(seeds=["red", "moon"], output=str(path), max_attempts=6,
                              transforms={"case":["upper"]}, combinations={"enabled":True}))
            self.assertEqual(path.read_bytes(), b"red\nRED\nmoon\nMOON\nredmoon\nREDMOON\n")
            self.assertEqual(result.stop_reason,"attempt_budget")

    def test_empty_wordlist_records_cannot_create_affixes(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/"in";source.write_bytes(b"\n\r\n")
            dest=Path(folder)/"out"
            run(Config(input=str(source),output=str(dest),transforms={"prefixes":["x"]}))
            self.assertEqual(dest.read_bytes(), b"")
