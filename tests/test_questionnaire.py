"""Behavior checks for the English questionnaire and multiple ordered sources."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from candidatelab.cli import main
from candidatelab.config import Config, parse_wordlist_paths
from candidatelab.engine import run
from candidatelab.wizard import collect


def answers_for(**overrides):
    """Named answers avoid coupling this questionnaire test to incidental text."""
    def read(question):
        for key, answer in overrides.items():
            if question.startswith(key):
                return answer() if callable(answer) else answer
        if question.startswith("Starting word"):
            return next(read.words)
        return ""
    read.words = iter(["Moon", ""])
    return read


class QuestionnaireTests(unittest.TestCase):
    def test_blank_choices_have_requested_defaults(self):
        messages = []
        config = collect(answers_for(), messages.append)
        self.assertEqual((config.min_length, config.max_length), (1, 40))
        self.assertEqual(config.languages, ("en",))
        self.assertEqual(config.transforms.case, ())
        self.assertTrue(any("starting words or phrases" in message for message in messages))
        self.assertTrue(any("pt (Portuguese)" in message for message in messages))

    def test_multiple_language_codes_and_legacy_locale(self):
        config = collect(answers_for(Languages="pt,en,es,fr,de,it"), lambda _: None)
        self.assertEqual(config.languages, ("pt", "en", "es", "fr", "de", "it"))
        self.assertEqual(Config(seeds=["word"], locale="pt-PT").languages, ("pt-pt",))
        self.assertEqual(Config(seeds=["word"], languages=["EN", "en", "pt-PT"]).languages, ("en", "pt-pt"))

    def test_bad_language_codes_are_rejected(self):
        for value in (["English"], [""], "pt,en", ["en", 4], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config(seeds=["word"], languages=value)

    def test_case_question_yes_enables_all_supported_variants(self):
        config = collect(answers_for(**{"Add case variants": "Y"}), lambda _: None)
        self.assertEqual(config.transforms.case, ("lower", "upper", "capitalized"))

    def test_case_question_reprompts_instead_of_accepting_all(self):
        responses = iter(["all", "yes"])
        messages=[]
        config = collect(answers_for(**{"Add case variants": lambda: next(responses)}), messages.append)
        self.assertEqual(config.transforms.case, ("lower", "upper", "capitalized"))
        self.assertIn("Please answer y or n. Press Enter for no.", messages)

    def test_csv_paths_include_quoted_commas(self):
        self.assertEqual(parse_wordlist_paths('one.txt, "two, words.txt", three.txt'), ("one.txt", "two, words.txt", "three.txt"))
        for value in ("one,,two", "one,", ',"unterminated'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_wordlist_paths(value)

    def test_wizard_records_multiple_paths(self):
        config = collect(answers_for(**{"Input wordlist": "first.txt, second.txt"}), lambda _: None)
        self.assertEqual(config.inputs, (str(Path("first.txt").resolve()), str(Path("second.txt").resolve())))
        self.assertIsNone(config.input)

    def test_multiple_files_preserve_order_and_share_dedupe(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); first=root/"a"; second=root/"b"; dest=root/"out"
            first.write_text("one\nshared\n"); second.write_text("shared\ntwo\n")
            result=run(Config(inputs=[str(first), str(second)], output=str(dest)))
            self.assertEqual(dest.read_bytes(), b"one\nshared\ntwo\n")
            self.assertEqual((result.accepted,result.duplicates), (3,1))

    def test_invalid_second_source_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); first=root/"a"; dest=root/"out"; first.write_text("one\n")
            with self.assertRaises(FileNotFoundError):
                run(Config(inputs=[str(first),str(root/"missing")], output=str(dest)))
            self.assertFalse(dest.exists())

    def test_multiple_config_paths_resolve_beside_json_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"config.json"
            config=Config(inputs=["a.txt","b.txt"], languages=["pt","en"])
            config.save(path)
            loaded=Config.load(path)
            self.assertEqual(loaded.inputs, tuple(str((path.parent/name).resolve()) for name in config.inputs))
            self.assertEqual(loaded.languages,config.languages)

    def test_cli_source_override_replaces_both_legacy_and_new_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); config=root/"config.json"; dest=root/"out"
            first=root/"a"; second=root/"b"; third=root/"c"
            for path in (first,second,third): path.write_text(path.name+"\n")
            Config(input="missing.txt", inputs=["also-missing.txt"], output=str(dest)).save(config)
            with redirect_stderr(io.StringIO()):
                code=main(["generate","--config",str(config),"--input",f"{first},{second}","--input",str(third)])
            self.assertEqual(code,0)
            self.assertEqual(dest.read_bytes(),b"a\nb\nc\n")

    def test_global_budgets_apply_across_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); a=root/"a"; b=root/"b"; dest=root/"out"
            a.write_text("one\n"); b.write_text("two\nthree\n")
            result=run(Config(inputs=[str(a),str(b)],output=str(dest),max_candidates=2))
            self.assertEqual(dest.read_bytes(),b"one\ntwo\n")
            self.assertEqual(result.stop_reason,"candidate_budget")

    def test_default_character_bound_applies_to_cli_and_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out"
            result=run(Config(seeds=["x", "a"*40,"b"*41],output=str(output)))
            self.assertEqual(result.accepted,2)
            self.assertEqual(result.filtered,1)

    def test_invalid_source_collections_are_rejected(self):
        for sources in ("file.txt", [""], [7], ["a"]*33):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                Config(inputs=sources)
