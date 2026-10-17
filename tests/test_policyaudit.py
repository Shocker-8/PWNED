"""Focused offline checks for the defensive policy-audit MVP.

A scripted fake agent stands in for Gemma here; the mandatory live-model check
lives in test_policyaudit_live.py and is not replaced by these mocks.
"""
import json

import pytest

from candidatelab import policyaudit as pa
from candidatelab.policyaudit import (PolicyInterview, PolicyModel, RuntimeConfigError,
                                      conduct_policy_audit, derive_policy, synthesize,
                                      validate_candidate, write_corpus)


class ScriptedAgent:
    model = pa.REQUIRED_MODEL

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, payload):
        self.calls.append(payload)
        return self.responses.pop(0)


def _config(tmp_path, **override):
    data = {
        "model": "lmstudio/qwen3.8-27b",
        "small_model": "lmstudio/google/gemma-4-12b-qat",
        "provider": {"lmstudio": {"options": {"baseURL": "http://127.0.0.1:1234/v1"}}},
    }
    data.update(override)
    path = tmp_path / "rt.json"
    path.write_text(json.dumps(data))
    return path


# --- runtime-model resolution -----------------------------------------
def test_resolver_picks_gemma_from_small_model_not_qwen(tmp_path):
    base, model = pa.resolve_runtime_model(str(_config(tmp_path)))
    assert model == pa.REQUIRED_MODEL and base == "http://127.0.0.1:1234/v1"


def test_resolver_precedence_prefers_runtime_model_key(tmp_path):
    path = _config(tmp_path, runtime_model="lmstudio/google/gemma-4-12b-qat")
    base, model = pa.resolve_runtime_model(str(path))
    assert model == pa.REQUIRED_MODEL


def test_resolver_rejects_config_without_a_usable_model(tmp_path):
    path = _config(tmp_path, model="lmstudio/qwen3.8-27b", small_model="lmstudio/other")
    with pytest.raises(RuntimeConfigError, match="runtime_model"):
        pa.resolve_runtime_model(str(path))


def test_runtime_model_override_allows_any_local_model(tmp_path):
    path = _config(tmp_path, runtime_model="lmstudio/llama-3.1-8b-instruct")
    base, model = pa.resolve_runtime_model(str(path))
    assert model == "llama-3.1-8b-instruct" and base == "http://127.0.0.1:1234/v1"


def test_resolver_rejects_non_loopback_endpoint(tmp_path):
    path = _config(tmp_path, provider={"lmstudio": {"options": {"baseURL": "http://example.com/v1"}}})
    with pytest.raises(RuntimeConfigError, match="loopback"):
        pa.resolve_runtime_model(str(path))


# --- interview controller ---------------------------------------------
def test_interview_extracts_facts_and_does_not_reask():
    session = PolicyInterview("account")
    agent = ScriptedAgent([
        {"facts": [{"key": "min_length", "value": "12", "evidence": "the minimum is 12"}],
         "unknown_facts": [], "next_topic": "max_length"}])
    nxt = session.step("the minimum is 12 characters", agent)
    assert nxt == "max_length"
    assert session.facts["min_length"]["value"] == "12"
    assert "min_length" not in session.available  # never offered again


def test_interview_tracks_explicit_unknowns_separately():
    session = PolicyInterview("account")
    agent = ScriptedAgent([
        {"facts": [], "unknown_facts": [{"key": "normalization", "evidence": "normalization is unknown"}],
         "next_topic": None}])
    session.step("normalization is unknown here", agent)
    assert "normalization" in session.unknown and "normalization" not in session.facts


def test_interview_rejects_evidence_not_in_text():
    session = PolicyInterview("account")
    agent = ScriptedAgent([
        {"facts": [{"key": "min_length", "value": "12", "evidence": "fabricated quote"}],
         "unknown_facts": [], "next_topic": None}])
    with pytest.raises(ValueError, match="verbatim"):
        session.step("the policy text says nothing of the sort", agent)


def test_interview_rejects_reproposed_known_topic():
    session = PolicyInterview("account")
    agent = ScriptedAgent([
        {"facts": [{"key": "min_length", "value": "12", "evidence": "minimum 12"}],
         "unknown_facts": [], "next_topic": "min_length"}])
    with pytest.raises(ValueError, match="answered"):
        session.step("minimum 12", agent)


def test_interview_back_revisits_last_topic():
    session = PolicyInterview("account")
    session.facts = {"min_length": {"value": "12", "evidence": "min 12"}}
    session.asked = ["min_length"]
    session.go_back()
    assert "min_length" not in session.facts and "min_length" in session.available


# --- policy derivation + validation -----------------------------------
def test_derive_policy_defaults_by_context():
    account = derive_policy("account", {})
    wifi = derive_policy("wifi", {})
    assert (account.min_length, account.max_length) == (1, 40)
    assert (wifi.min_length, wifi.max_length, wifi.unicode_allowed) == (8, 63, False)


def test_derive_policy_notes_contradiction_with_generic_bounds():
    policy = derive_policy("account", {"max_length": {"value": "64", "evidence": "max 64"}})
    assert policy.max_length == 64 and any("generic" in note for note in policy.notes)


def test_derive_policy_rejects_min_exceeding_max():
    facts = {"min_length": {"value": "20", "evidence": "min 20"},
             "max_length": {"value": "10", "evidence": "max 10"}}
    with pytest.raises(ValueError, match="exceeds"):
        derive_policy("account", facts)


def test_validate_candidate_enforces_each_rule():
    policy = PolicyModel("account", 8, 16, frozenset({"digit"}), False, False, "none")
    assert validate_candidate(policy, "abcd1234")[0]
    assert not validate_candidate(policy, "abcdefgh")[0]      # no digit
    assert not validate_candidate(policy, "abc123")[0]        # too short
    assert not validate_candidate(policy, "abc 1234")[0]      # space
    assert not validate_candidate(policy, "café1234")[0]      # non-ascii


# --- synthetic corpus --------------------------------------------------
def test_synthesize_is_deterministic_deduped_and_control_free():
    policy = derive_policy("account", {"min_length": {"value": "8", "evidence": "min 8"},
                                       "character_requirements": {"value": "digit", "evidence": "digit"}})
    corpus_a, report_a = synthesize(policy)
    corpus_b, report_b = synthesize(policy)
    assert corpus_a == corpus_b                       # reproducible
    assert len(corpus_a) == len(set(corpus_a))        # exactly deduplicated
    assert all("\n" not in line and "\r" not in line for line in corpus_a)
    assert all(case["matches_expectation"] for case in report_a["cases"])


def test_write_corpus_writes_report_and_refuses_overwrite(tmp_path):
    policy = derive_policy("account", {})
    corpus, report = synthesize(policy)
    out = tmp_path / "corpus.txt"
    size, report_path = write_corpus(out, corpus, report)
    assert out.read_text().splitlines() == corpus and size > 0
    assert json.loads((tmp_path / "corpus.txt.report.json").read_text())["case_count"] == len(corpus)
    with pytest.raises(FileExistsError):
        write_corpus(out, corpus, report)


# --- end-to-end flow (scripted agent) ---------------------------------
def _flow(tmp_path, context, reads, responses):
    out = tmp_path / "flow.txt"
    writes = []
    reader = iter(reads)
    agent = ScriptedAgent(responses)
    result = conduct_policy_audit(context, str(out), agent,
                                  lambda q: next(reader), writes.append)
    return out, result, writes


def test_account_flow_produces_corpus_and_report(tmp_path):
    reads = ["min 12 and digits", "", "the maximum is 20", "y"]
    responses = [
        {"facts": [{"key": "min_length", "value": "12", "evidence": "min 12"},
                   {"key": "character_requirements", "value": "digits", "evidence": "min 12 and digits"}],
         "unknown_facts": [], "next_topic": "max_length"},
        {"facts": [{"key": "max_length", "value": "20", "evidence": "the maximum is 20"}],
         "unknown_facts": [], "next_topic": None}]
    out, result, _ = _flow(tmp_path, "account", reads, responses)
    assert result["written"] and result["gemma_participated"]
    assert result["model"] == pa.REQUIRED_MODEL
    assert out.exists() and (tmp_path / "flow.txt.report.json").exists()


def test_wifi_flow_completes(tmp_path):
    reads = ["ascii only, min 10", "", "n"]  # decline at review
    responses = [
        {"facts": [{"key": "min_length", "value": "10", "evidence": "min 10"},
                   {"key": "unicode_policy", "value": "ascii only", "evidence": "ascii only, min 10"}],
         "unknown_facts": [], "next_topic": None}]
    out, result, _ = _flow(tmp_path, "wifi", reads, responses)
    assert result["written"] is False
    assert not out.exists()  # declining review writes nothing


def test_conduct_refuses_when_a_session_already_exists(tmp_path):
    out = tmp_path / "flow.txt"
    (tmp_path / "flow.txt.session.json").write_text("{}")
    with pytest.raises(FileExistsError, match="resume"):
        conduct_policy_audit("account", str(out), ScriptedAgent([]), lambda q: "", lambda m: None)


# --- session save / load / resume -------------------------------------
def test_session_round_trip(tmp_path):
    interview = PolicyInterview("wifi")
    interview.facts = {"min_length": {"value": "8", "evidence": "min 8"}}
    interview.unknown = ["normalization"]
    interview.asked = ["min_length", "normalization"]
    interview.rounds = 2
    path = tmp_path / "s.session.json"
    pa.save_session(path, interview, tmp_path / "c.txt", pa.REQUIRED_MODEL)
    loaded, meta = pa.load_session(path)
    assert loaded.context == "wifi" and loaded.facts["min_length"]["value"] == "8"
    assert loaded.unknown == ["normalization"] and meta["model"] == pa.REQUIRED_MODEL


def test_load_session_rejects_malformed_file(tmp_path):
    path = tmp_path / "bad.session.json"
    path.write_text('{"version": 1, "kind": "something-else"}')
    with pytest.raises(ValueError, match="supported"):
        pa.load_session(path)


def test_resume_continues_and_writes_corpus(tmp_path):
    out = tmp_path / "resumed.txt"
    interview = PolicyInterview("account")
    interview.facts = {"min_length": {"value": "10", "evidence": "min 10"}}
    interview.asked = ["min_length"]
    session_file = tmp_path / "resumed.txt.session.json"
    pa.save_session(session_file, interview, out, pa.REQUIRED_MODEL)
    agent = ScriptedAgent([{"facts": [], "unknown_facts": [], "next_topic": None}])
    reads = iter(["y"])  # confirm corpus generation
    result = pa.resume_policy_audit(str(session_file), agent, lambda q: next(reads), lambda m: None)
    assert result["written"] and out.exists()
    assert (tmp_path / "resumed.txt.report.json").exists()


def test_account_flow_leaves_a_resumable_session(tmp_path):
    reads = ["min 12", "", "finish", "n"]  # decline corpus, keep session
    responses = [{"facts": [{"key": "min_length", "value": "12", "evidence": "min 12"}],
                  "unknown_facts": [], "next_topic": None}]
    out, result, _ = _flow(tmp_path, "account", reads, responses)
    assert result["written"] is False
    assert (tmp_path / "flow.txt.session.json").exists()
