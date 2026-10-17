from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from candidatelab.config import Config
from candidatelab.cli import main
from candidatelab.engine import run
from candidatelab.wizard import collect, initialize


class ConfigurationTests(unittest.TestCase):
    def test_reject_unknown_fields_and_bad_types(self):
        for extra in ({"typo": 1}, {"version": 2}, {"version": True},
                      {"max_candidates": False}, {"max_attempts": -1},
                      {"seeds": "word"}, {"seeds": [1]}, {"locale": None},
                      {"min_length": 4, "max_length": 3}, {"max_length": 4097},
                      {"encoding": "latin1"}, {"mode": "pairs"},
                      {"transforms": ["leet"]}, {"priorities": ["custom"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                Config.from_dict({"seeds": ["fictional"], **extra})

    def test_seed_constraints(self):
        for seeds in ([""], ["a\nb"], ["a\rb"], ["a" * 4097], ["a"] * 257):
            with self.subTest(seeds=str(seeds)[:50]), self.assertRaises(ValueError):
                Config(seeds=seeds)

    def test_explicit_zero_and_unset(self):
        config = Config(seeds=["word"], min_length=0, max_length=0, max_candidates=0)
        self.assertEqual(config.max_length, 0)
        self.assertEqual(Config(seeds=["word"]).max_length, 40)
        self.assertEqual(Config(seeds=["word"]).min_length, 1)
        self.assertIsNone(Config(seeds=["word"], max_length=None).max_length)
        self.assertEqual(Config.from_dict(asdict(config)), config)

    def test_paths_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = Config(input="words.txt", output="out.txt")
            config.save(path)
            loaded = Config.load(path)
            self.assertEqual(loaded.input, str((Path(folder) / "words.txt").resolve()))
            self.assertEqual(loaded.output, str((Path(folder) / "out.txt").resolve()))
            with self.assertRaises(FileExistsError):
                config.save(path)

    def test_duplicate_json_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text('{"seeds":["a"],"version":1,"version":2}')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                Config.load(path)

    def test_bounded_config_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_bytes(b" " * 262145)
            with self.assertRaisesRegex(ValueError, "256 KiB"):
                Config.load(path)

    def test_cli_precedence_and_clean_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            Config(seeds=["one", "two"], output="out.txt", max_candidates=2).save(path)
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["generate", "--config", str(path), "--max-candidates", "0"])
            self.assertEqual(code, 0)
            self.assertEqual((Path(folder) / "out.txt").read_bytes(), b"")
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("candidate_budget", stderr.getvalue())

    def test_cli_seeds_replace_config_seeds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            Config(seeds=["one"], output="out.txt").save(path)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["generate", "--config", str(path), "--seed", "two"]), 0)
            self.assertEqual((Path(folder) / "out.txt").read_bytes(), b"two\n")

    def test_missing_batch_values_does_not_prompt(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["generate"]), 1)

    def test_init_noninteractive_does_not_prompt(self):
        with patch("sys.stdin.isatty", return_value=False), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["init"]), 1)

    def test_character_constraints_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "out"
            result = run(Config(seeds=["a", "éé", "longword", "éé"], output=str(dest), min_length=2, max_length=2))
            self.assertEqual(dest.read_bytes(), "éé\n".encode())
            self.assertEqual((result.attempted, result.accepted, result.filtered, result.duplicates), (4, 1, 2, 1))

    def test_attempt_and_byte_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            for number, extra, expected, reason in (
                (0, {"max_attempts": 0}, b"", "attempt_budget"),
                (1, {"max_attempts": 1}, b"one\n", "attempt_budget"),
                (2, {"max_output_bytes": 3}, b"", "byte_budget"),
                (3, {"max_output_bytes": 4}, b"one\n", "byte_budget"),
            ):
                path = Path(folder) / str(number)
                result = run(Config(seeds=["one", "two"], output=str(path), **extra))
                self.assertEqual(path.read_bytes(), expected)
                self.assertEqual(result.stop_reason, reason)

    def test_filtered_input_still_consumes_attempt_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run(Config(seeds=["a", "b", "long"], output=str(Path(folder)/"out"), min_length=3, max_attempts=2))
            self.assertEqual((result.attempted, result.accepted, result.stop_reason), (2, 0, "attempt_budget"))

    def test_wizard_equivalent_to_handwritten_configuration(self):
        answers = iter(["", "", "", " one ", "two", "", "", "", "0", "10", "100", "", "", "", "", "", "out.txt"])
        config = collect(lambda _: next(answers), lambda _: None)
        expected = Config(seeds=[" one ", "two"], output=str(Path("out.txt").resolve()), max_candidates=0, max_attempts=10, max_output_bytes=100)
        self.assertEqual(config, expected)

    def test_wizard_save_and_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            for response, saved in (("y", True), ("n", False)):
                path = Path(folder) / f"{response}.json"
                answers = iter(["", "", "", "word", "", "", "", "", "", "", "", "", "", "", "", "", response])
                initialize(path, lambda _: next(answers), lambda _: None)
                self.assertEqual(path.exists(), saved)
                if saved:
                    self.assertEqual(Config.load(path).seeds, ("word",))

    def test_wizard_combination_choices(self):
        answers = iter(["", "", "", "red", "moon", "", "", "", "", "", "", "", "", "", "", "y", "2", "-", "10", "out.txt"])
        config = collect(lambda _: next(answers), lambda _: None)
        self.assertTrue(config.combinations.enabled)
        self.assertEqual(config.combinations.separators, ("-",))
        self.assertEqual(config.combinations.max_combinations, 10)
