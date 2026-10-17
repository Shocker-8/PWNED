from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from candidatelab.cli import main
from candidatelab.config import Config
from candidatelab.engine import run
from candidatelab.planner import plan

class PlanTests(unittest.TestCase):
    def test_simple_bound_is_not_claimed_unique_count(self):
        result=plan(Config(seeds=["same","same"]))
        self.assertEqual(result["raw_expansion_upper"],2)
        self.assertEqual(result["emitted_upper"],2)
        self.assertIsNone(result["exact_unique_count"])
        self.assertIsNone(result["estimated_seconds"])

    def test_combinations_and_variants_bound_without_enumeration(self):
        config=Config(seeds=["a","b"],transforms={"case":["upper"]},combinations={"enabled":True})
        self.assertEqual(plan(config)["raw_expansion_upper"],8)

    def test_wordlists_not_opened_or_output_created(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            result=plan(Config(input=str(Path(folder)/"missing"),output=str(output)))
            self.assertIsNone(result["raw_expansion_upper"])
            self.assertFalse(output.exists())

    def test_zero_caps_and_profile_limit(self):
        self.assertEqual(plan(Config(seeds=["a"],max_candidates=0))["emitted_upper"],0)
        self.assertEqual(plan(Config(seeds=["a"],max_attempts=0))["emitted_upper"],0)
        self.assertEqual(plan(Config(seeds=["a"],max_output_bytes=0))["emitted_upper"],0)
        result=plan(Config(mode="pairs",output_format="jsonl",seeds=["a","b"],profiles=[{"id":"one","usernames":["name"],"max_candidates":1}]))
        self.assertEqual(result["emitted_upper"],1)

    def test_wifi_effective_limits(self):
        result=plan(Config(seeds=["word"],preset="wpa2"))
        self.assertEqual(result["effective_password_lengths"],{"minimum":8,"maximum":40})

    def test_bound_covers_actual_small_generated_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, transform in enumerate(({}, {"case":["lower","upper"]}, {"leet":{"a":["@"]},"prefixes":["x"]})):
                config=Config(seeds=["aa","bb"],output=str(Path(folder)/str(index)),transforms=transform,combinations={"enabled":True,"allow_reuse":True,"max_tokens":3})
                predicted=plan(config); actual=run(config)
                self.assertLessEqual(actual.attempted,predicted["raw_expansion_upper"])
                self.assertLessEqual(actual.accepted,predicted["emitted_upper"])

    def test_cli_plan_is_json_and_does_not_generate(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            stream=io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["plan","--seed","word","--output",str(output)]),0)
            self.assertEqual(json.loads(stream.getvalue())["mode"],"passwords")
            self.assertFalse(output.exists())

    def test_explain_keeps_generation_stdout_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out";stdout=io.StringIO();stderr=io.StringIO()
            with redirect_stdout(stdout),redirect_stderr(stderr):
                self.assertEqual(main(["generate","--seed","word","--output",str(output),"--explain"]),0)
            self.assertEqual(stdout.getvalue(),"")
            self.assertIn("Emitted upper bound: 1",stderr.getvalue())
            self.assertEqual(output.read_bytes(),b"word\n")
