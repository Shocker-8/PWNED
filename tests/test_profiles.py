from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from candidatelab.config import Config
from candidatelab.engine import run
from candidatelab.profiles import Profile, username_collisions
from candidatelab.wizard import collect
from test_questionnaire import answers_for


class ProfileTests(unittest.TestCase):
    def test_manual_handles_preserved_and_templates_separate(self):
        profile=Profile(id="one",usernames=["Exact.User"],first_name="Mary Jane",last_name="Smith",templates=["first.last","first-initial+last"])
        self.assertEqual(profile.resolved_usernames,("Exact.User","maryjane.smith","msmith"))

    def test_template_unicode_and_case_policy(self):
        profile=Profile(id="one",first_name="E\u0301va",last_name="Nunes",templates=["first.last"],template_case="preserve")
        self.assertEqual(profile.resolved_usernames,("Éva.Nunes",))

    def test_collision_reporting_and_unique_handles(self):
        one=Profile(id="one",usernames=["shared","shared"])
        two=Profile(id="two",usernames=["shared","other"])
        self.assertEqual(one.resolved_usernames,("shared",))
        self.assertEqual(username_collisions([one,two]),{"shared":["one","two"]})

    def test_invalid_profiles(self):
        for data in ({"id":"../bad"},{"id":"one","usernames":["a\nb"]},
                     {"id":"one","templates":["unsupported"]},{"id":"one","templates":["first.last"]},
                     {"id":"one","max_candidates":True},{"id":"one","usernames":["é"*129]},
                     {"id":"one","seeds":["x"*4097]},{"wrong":1}):
            with self.subTest(data=data),self.assertRaises(ValueError):
                Profile.from_dict(data)

    def test_invalid_mode_contracts(self):
        base={"mode":"pairs","output_format":"jsonl","profiles":[{"id":"one","usernames":["a"],"seeds":["word"]}]}
        for extra in ({"output_format":"text"},{"profiles":[]},
                      {"profiles":[{"id":"one","seeds":["word"]}]},
                      {"profiles":[{"id":"one","usernames":["a"]}]},
                      {"profiles":[base["profiles"][0],base["profiles"][0]]}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                Config.from_dict({**base,**extra})

    def test_same_password_survives_for_two_usernames_and_context_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            config=Config(mode="pairs",output_format="jsonl",output=str(path),profiles=[
                {"id":"one","usernames":["first"],"seeds":["shared","private-one"]},
                {"id":"two","usernames":["second"],"seeds":["shared","private-two"]}])
            result=run(config)
            records=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([(r["username"],r["password_candidate"]) for r in records],
                             [("first","shared"),("second","shared"),("first","private-one"),("second","private-two")])
            self.assertEqual(result.profile_counts,{"one":2,"two":2})
            self.assertEqual(result.sha256,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_global_and_profile_budgets_fair(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            config=Config(mode="pairs",output_format="jsonl",seeds=["one","two","three"],output=str(path),max_candidates=3,
                          profiles=[{"id":"one","usernames":["a"],"max_candidates":1},{"id":"two","usernames":["b"]}])
            result=run(config)
            self.assertEqual(result.profile_counts,{"one":1,"two":2})
            self.assertEqual(result.stop_reason,"candidate_budget")
            self.assertEqual([json.loads(x)["username"] for x in path.read_text().splitlines()],["a","b","b"])

    def test_complete_pair_dedupe_ignores_profile_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(mode="pairs",output_format="jsonl",seeds=["same"],output=str(path),profiles=[
                {"id":"one","usernames":["shared"]},{"id":"two","usernames":["shared"]}]))
            self.assertEqual((result.accepted,result.duplicates,result.username_collision_count),(1,1,1))

    def test_pair_byte_budget_uses_serialized_json_not_password_size(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(mode="pairs",output_format="jsonl",seeds=["word"],output=str(path),max_output_bytes=5,
                              profiles=[{"id":"one","usernames":["a"]}]))
            self.assertEqual((path.read_bytes(),result.stop_reason),(b"","byte_budget"))

    def test_delimiters_are_unambiguous_in_jsonl(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            run(Config(mode="pairs",output_format="jsonl",seeds=['p:ass"word'],output=str(path),profiles=[{"id":"one","usernames":['user:name"']}]))
            record=json.loads(path.read_text())
            self.assertEqual(record["username"],'user:name"')
            self.assertEqual(record["password_candidate"],'p:ass"word')

    def test_profile_file_paths_roundtrip_and_no_cross_profile_leak(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); config_path=root/"config.json"
            (root/"a.txt").write_text("only-one\n");(root/"b.txt").write_text("only-two\n")
            config=Config(mode="pairs",output_format="jsonl",output="out",profiles=[
                {"id":"one","usernames":["a"],"inputs":["a.txt"]},{"id":"two","usernames":["b"],"inputs":["b.txt"]}])
            config.save(config_path);loaded=Config.load(config_path)
            self.assertEqual(Config.from_dict(asdict(loaded)),loaded)
            run(loaded)
            records=[json.loads(line) for line in (root/"out").read_text().splitlines()]
            self.assertEqual([(r["username"],r["password_candidate"]) for r in records],[("a","only-one"),("b","only-two")])

    def test_username_mode_ignores_password_character_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(mode="usernames",max_length=1,output=str(path),profiles=[{"id":"one","usernames":["LongHandle"]}]))
            self.assertEqual(path.read_bytes(),b"LongHandle\n")
            self.assertEqual(result.accepted,1)

    def test_username_mode_rejects_password_inputs(self):
        with self.assertRaisesRegex(ValueError,"not password"):
            Config(mode="usernames",seeds=["word"],profiles=[{"id":"one","usernames":["name"]}])

    def test_zero_profile_budget_skips_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(mode="usernames",output=str(path),profiles=[{"id":"one","usernames":["name"],"max_candidates":0}]))
            self.assertEqual((result.accepted,result.stop_reason),(0,"profile_budget"))

    def test_username_wizard_does_not_ask_password_questions(self):
        asked=[]
        reader=answers_for(Choose="3",**{"Supplied usernames":"Exact.User"})
        def read(question):
            asked.append(question)
            return reader(question)
        config=collect(read,lambda _:None)
        self.assertEqual(config.mode,"usernames")
        self.assertEqual(config.profiles[0].resolved_usernames,("Exact.User",))
        self.assertFalse(any("password characters" in question or question.startswith("Input wordlist") for question in asked))

    def test_pair_wizard_collects_private_seed_and_jsonl(self):
        private_words=iter(["copper", ""])
        config=collect(answers_for(Choose="4",**{"Supplied usernames":"Exact.User", "Profile starting word": lambda: next(private_words)}),lambda _:None)
        self.assertEqual((config.mode,config.output_format),("pairs","jsonl"))
        self.assertEqual(config.profiles[0].seeds,("copper",))
        self.assertTrue(config.output.endswith("candidates.jsonl"))
