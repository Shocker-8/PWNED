import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
from candidatelab.config import Config
from candidatelab.cli import main
from candidatelab.engine import run
from candidatelab.mutations import mutations, family_order
from candidatelab.transforms import TransformSettings
from candidatelab.planner import plan


class SchedulingTests(unittest.TestCase):
    def test_explicit_priorities_keep_original_first_and_bound_prefix(self):
        settings=TransformSettings(case=["upper"],prefixes=["X"],max_variants=3)
        items=list(mutations("aa",settings,["affix","case"]))
        self.assertEqual([x.value for x in items],["aa","Xaa","XAA"])
        self.assertEqual([x.family for x in items],["original","affix","affix"])
        self.assertEqual([x.edit_cost for x in items],[0,1,2])
        self.assertEqual(family_order(["leet"]),("original","leet","normalization","case","affix"))

    def test_affix_before_huge_leet_space_is_still_bounded(self):
        settings=TransformSettings(prefixes=["X"],leet={"a":["@","4"]},max_replaced=3,max_variants=2)
        self.assertEqual(len(list(mutations("a"*4096,settings,["affix"]))),2)

    def test_family_quota_skipped_records_still_consume_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(seeds=["a","b"],output=str(path),transforms={"case":["upper"]},family_quotas={"original":0,"case":1}))
            self.assertEqual(path.read_bytes(),b"A\n")
            self.assertEqual((result.attempted,result.quota_skipped),(4,3))
            self.assertEqual(result.family_attempts,{"case":1})

    def test_zero_quotas_do_not_evade_global_attempt_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(seeds=["a","b","c"],output=str(path),family_quotas={"original":0},max_attempts=2))
            self.assertEqual((result.accepted,result.attempted,result.stop_reason),(0,2,"attempt_budget"))

    def test_round_robin_sources_preserve_source_rank(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=root/"a";b=root/"b";out=root/"out"
            a.write_text("A1\nA2\n");b.write_text("B1\nB2\n")
            result=run(Config(seeds=["a","b"],inputs=[str(a),str(b)],output=str(out),source_scheduling="round_robin"))
            self.assertEqual(out.read_bytes(),b"a\nA1\nB1\nb\nA2\nB2\n")
            self.assertEqual(result.input_records,4)

    def test_blank_source_records_are_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"in";source.write_bytes(b"\n"*10)
            result=run(Config(input=str(source),output=str(root/"out"),max_input_records=2))
            self.assertEqual((result.input_records,result.accepted,result.stop_reason),(2,0,"input_record_budget"))

    def test_cancellation_and_zero_time_budget_produce_valid_empty_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name, extra in (("cancelled",{}),("time_budget",{"max_seconds":0})):
                path=root/name
                result=run(Config(seeds=["a"],output=str(path),**extra),cancelled=(lambda:True) if name=="cancelled" else None)
                self.assertEqual(path.read_bytes(),b"")
                self.assertEqual(result.stop_reason,name)
                self.assertEqual(result.sha256,hashlib.sha256(b"").hexdigest())

    def test_elapsed_budget_stops_mid_run_using_injected_clock(self):
        class Clock:
            calls=0
            def __call__(self):
                self.calls+=1
                return 0.0 if self.calls<8 else 2.0
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            result=run(Config(seeds=[str(x) for x in range(20)],output=str(path),max_seconds=1),clock=Clock())
            self.assertEqual(result.stop_reason,"time_budget")
            self.assertGreater(result.accepted,0)
            self.assertLess(result.accepted,20)
            self.assertTrue(path.read_bytes().endswith(b"\n"))

    def test_provenance_has_source_rank_family_and_edit_cost(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            run(Config(seeds=["moon"],output=str(path),output_format="jsonl",provenance=True,transforms={"case":["upper"]}))
            records=[json.loads(row) for row in path.read_text().splitlines()]
            self.assertEqual(records[1]["password_candidate"],"MOON")
            self.assertEqual(records[1]["provenance"],{"profile_id":"default","source_kind":"seed","source_index":0,"source_rank":0,"family":"case","edit_cost":1})

    def test_repeated_count_bounded_runs_have_identical_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            outputs=[]
            for i in range(2):
                path=Path(folder)/str(i)
                run(Config(seeds=["one","two"],output=str(path),max_candidates=4,priorities=["affix"],transforms={"case":["upper"],"suffixes":["42"]}))
                outputs.append(path.read_bytes())
            self.assertEqual(outputs[0],outputs[1])

    def test_invalid_priority_budget_and_provenance_settings(self):
        for extra in ({"priorities":["case","case"]},{"priorities":["unknown"]},{"family_quotas":{"case":-1}},
                      {"family_quotas":{"x":2}},{"max_seconds":float("nan")},{"max_seconds":True},
                      {"max_input_records":False},{"provenance":True},{"source_scheduling":"random"}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                Config.from_dict({"seeds":["word"],**extra})

    def test_cli_controls_and_planner_share_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"out"
            with redirect_stderr(io.StringIO()):
                code=main(["generate","--seed","word","--output",str(path),"--max-seconds","0","--priority","case","--family-quota","original=0"])
            self.assertEqual(code,0)
            self.assertEqual(path.read_bytes(),b"")
            description=plan(Config(seeds=["word"],priorities=["leet"],family_quotas={"case":4},max_seconds=2))
            self.assertEqual(description["custom_priority_tiers"],list(family_order(["leet"])))
            self.assertEqual(description["budgets"]["seconds"],2)
