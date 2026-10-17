import itertools
from pathlib import Path
import tempfile
import unittest
from candidatelab.config import Config
from candidatelab.engine import run
from candidatelab.transforms import TransformSettings, variants

class TransformationTests(unittest.TestCase):
    def test_default_preserves_exact_original(self):
        self.assertEqual(list(variants(" café ", TransformSettings())), [" café "])

    def test_case_order_and_capitalized_semantics(self):
        self.assertEqual(list(variants("aBC", TransformSettings(case=["lower", "upper", "capitalized"]))), ["aBC", "abc", "ABC", "Abc"])

    def test_leet_position_limits_and_order(self):
        settings = TransformSettings(leet={"a": ["@", "4"]}, max_replaced=1)
        self.assertEqual(list(variants("aa", settings)), ["aa", "@a", "4a", "a@", "a4"])
        self.assertEqual(list(variants("aa", TransformSettings(leet={"a": ["@"]}, max_replaced=2))), ["aa", "@a", "a@", "@@"])

    def test_limit_applies_before_explosive_enumeration(self):
        settings = TransformSettings(leet={"a": ["@", "4"]}, max_replaced=3, max_variants=2)
        self.assertEqual(len(list(variants("a"*4096, settings))), 2)

    def test_affixes_apply_once_and_preserve_spaces(self):
        self.assertEqual(list(variants("word", TransformSettings(prefixes=[" "], suffixes=["!"]))), ["word", "word!", " word", " word!"])

    def test_original_counts_toward_cap(self):
        self.assertEqual(list(variants("word", TransformSettings(case=["upper"], max_variants=1))), ["word"])

    def test_unicode_constraints_after_case_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out"
            result = run(Config(seeds=["ß"], output=str(path), max_length=1, transforms={"case": ["upper"]}))
            self.assertEqual(path.read_bytes(), "ß\n".encode())
            self.assertEqual(result.filtered, 1)

    def test_duplicate_variants_spend_attempts_and_dedupe_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out"
            result = run(Config(seeds=["word"], output=str(path), max_attempts=2, transforms={"case": ["lower", "upper"]}))
            self.assertEqual(path.read_bytes(), b"word\n")
            self.assertEqual((result.attempted,result.duplicates,result.stop_reason),(2,1,"attempt_budget"))

    def test_invalid_settings(self):
        for data in ({"case":["random"]},{"max_replaced":4},{"max_variants":0},{"max_variants":True},{"leet":{"aa":["x"]}},{"leet":{"a":"@"}},{"prefixes":["\n"]},{"unknown":1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                TransformSettings.from_dict(data)
