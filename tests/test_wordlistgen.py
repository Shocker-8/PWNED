"""Offline checks for the custom wordlist generator (bopscrk-style)."""
import pytest

from candidatelab import wordlistgen as W


class FakeAgent:
    model = "google/gemma-4-12b-qat"

    def __init__(self, scripts, expansions=None):
        self.scripts = list(scripts)
        self._expansions = expansions

    def extract(self, text, known, asked):
        return self.scripts.pop(0)

    def expand(self, profile_text, known):
        if self._expansions is None:
            return {"words": []}
        return self._expansions


def test_accept_extraction_keeps_derived_words_and_sanitizes():
    # The model is allowed to DERIVE words (biggie from "fat", benfica from Lisbon)
    # that are not literally in the operator's text. We only sanitise/dedupe.
    proposal = {"base_words": ["Nuno", "biggie", "big guy", "nuno", "bad\x07ctrl"],
                "numbers": ["1986", "age 39"], "dates": ["1986-05-02", "not-a-date"],
                "next_question": "Any pets?"}
    words, numbers, dates, question = W.accept_extraction(proposal, "")
    assert words == ["nuno", "biggie", "big", "guy", "badctrl"]  # lowercased, split, deduped, control stripped
    assert numbers == ["1986", "39"]                              # digits pulled from "age 39"
    assert dates == ["1986-05-02"]                                # invalid date dropped
    assert question == "Any pets?"


def test_accept_extraction_rejects_bad_schema():
    with pytest.raises(ValueError):
        W.accept_extraction({"base_words": []}, "text")


def test_post_retries_with_larger_budget_on_length_then_succeeds():
    client = W.TargetGemma.__new__(W.TargetGemma)  # no network/__init__
    calls = []

    def fake_request(system, payload, schema, name, max_tokens):
        calls.append(max_tokens)
        if len(calls) == 1:
            return "length", ""                      # reasoning ate the budget
        return "stop", '{"words": ["zelda"]}'          # succeeds with more room

    client._request = fake_request
    out = client._post("sys", {}, {}, "x", 2500)
    assert out == {"words": ["zelda"]}
    assert calls == [2500, 5000]                        # grew the budget once


def test_post_raises_clear_error_when_budget_exhausted():
    client = W.TargetGemma.__new__(W.TargetGemma)

    def always_length(system, payload, schema, name, max_tokens):
        return "length", ""

    client._request = always_length
    with pytest.raises(ValueError, match="token budget reasoning"):
        client._post("sys", {}, {}, "x", 8000)


def test_accept_expansions_sanitises_and_dedupes():
    proposal = {"words": [
        "zelda", "link",
        "nuno",        # already known -> dropped
        "bad word",    # has space -> dropped
        "zelda",       # duplicate -> dropped
    ]}
    out = W.accept_expansions(proposal, existing=["nuno"])
    assert out == ["zelda", "link"]


def test_accept_expansions_rejects_bad_schema():
    with pytest.raises(ValueError):
        W.accept_expansions({"nope": 1}, existing=[])


def test_parse_selection_variants():
    assert W._parse_selection("all", 3) == {0, 1, 2}
    assert W._parse_selection("none", 3) == set()
    assert W._parse_selection("1,3", 3) == {0, 2}
    assert W._parse_selection("", 3) == set()
    assert W._parse_selection("9", 3) == set()  # out of range ignored


def test_expand_phase_adds_selected_words():
    agent = FakeAgent([], expansions={"words": ["zelda", "link"]})
    reads = iter(["y", "1"])  # accept expansion, pick #1 only
    added = W._expand_phase(agent, lambda q: next(reads), lambda m: None, seeds=["nuno"], transcript="likes zelda")
    assert added == ["zelda"]


def test_expand_phase_declined_returns_nothing():
    agent = FakeAgent([], expansions={"words": ["zelda"]})
    reads = iter(["n"])
    added = W._expand_phase(agent, lambda q: next(reads), lambda m: None, seeds=[], transcript="likes zelda")
    assert added == []


def test_expand_phase_survives_model_failure():
    class Boom:
        model = "m"
        def expand(self, *a):
            raise ValueError("could not reach the local model")
    writes = []
    reads = iter(["y"])
    added = W._expand_phase(Boom(), lambda q: next(reads), writes.append, seeds=[], transcript="x")
    assert added == [] and any("Expansion unavailable" in line for line in writes)


def test_build_config_maps_knobs_to_engine_settings():
    config = W.build_config("account", ["nuno"], ["1986"], [], combine=True, case=True,
                            leet=True, min_length=4, max_length=16, inputs=(), output="o.txt")
    assert config.seeds == ("nuno",) and config.max_length == 16
    assert set(config.transforms.case) == {"lower", "upper", "capitalized"}
    assert config.transforms.leet and config.combinations.enabled
    assert config.combinations.numeric_tokens == ("1986",)


def test_wifi_context_uses_wpa2_preset_bounds():
    config = W.build_config("wifi", ["spot"], [], [], combine=False, case=False, leet=False,
                            min_length=8, max_length=63, inputs=(), output="o.txt")
    assert config.preset == "wpa2" and (config.min_length, config.max_length) == (8, 63)


def test_unique_output_appends_number(tmp_path):
    first = tmp_path / "wl.txt"
    first.write_text("x\n")
    chosen = W.unique_output(str(first))
    assert chosen.endswith("wl_1.txt")


def test_generate_writes_a_real_wordlist(tmp_path):
    out = tmp_path / "wl.txt"
    agent = FakeAgent([
        {"base_words": ["nuno", "portugal"], "numbers": ["1986"], "dates": [],
         "next_question": "Any pet names?"},
        {"base_words": ["rex"], "numbers": [], "dates": [], "next_question": None}])
    reads = iter([
        "nuno from portugal born 1986", "",   # description
        "the dog is rex",                      # one follow-up answer
        "n",                                    # decline model expansion
        "y", "y", "n",                         # combine / case / leet
        "4", "16",                             # min / max
        "",                                     # no extra wordlists
        str(out),                               # output path
    ])
    writes = []
    result = W.generate("account", str(out), agent, lambda q: next(reads), writes.append)
    assert result["written"] and result["accepted"] > 0
    lines = out.read_text().splitlines()
    assert "nuno" in lines and "Nuno" in lines and "NUNO" in lines
    assert lines == list(dict.fromkeys(lines))         # deduplicated
    assert all(4 <= len(line) <= 16 for line in lines)  # length filter honored


def test_session_round_trip_and_resume(tmp_path):
    out = tmp_path / "r.txt"
    W.save_session(str(out) + ".session.json", {"context": "account", "seeds": ["nuno"],
                   "numbers": [], "dates": [], "inputs": [], "output": str(out),
                   "combine": False, "case": True, "leet": False,
                   "min_length": 1, "max_length": 40, "model": W.REQUIRED_MODEL})
    data = W.load_session(str(out) + ".session.json")
    assert data["seeds"] == ["nuno"] and data["context"] == "account"

    # Resume without adding info -> regenerate straight away.
    reads = iter(["n",             # do not add more info
                  "y", "y", "n",   # combine / case / leet
                  "1", "40", "",   # lengths + no wordlists
                  str(out)])        # output
    writes = []
    result = W.resume(str(out) + ".session.json", FakeAgent([]), lambda q: next(reads), writes.append)
    from pathlib import Path
    # A session already existed for r.txt, so the output auto-numbers rather than overwriting.
    assert result["written"] and Path(result["output"]).exists()
    assert result["output"].endswith("r_1.txt")


def test_load_session_rejects_wrong_kind(tmp_path):
    path = tmp_path / "bad.session.json"
    path.write_text('{"version": 2, "kind": "policy-audit"}')
    with pytest.raises(ValueError):
        W.load_session(str(path))
