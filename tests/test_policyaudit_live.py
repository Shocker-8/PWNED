"""Mandatory live check: a real request must reach the required local Gemma model
and return a validated result that reflects the stated policy.

This is NOT a substitute for, nor substituted by, the mocked tests. It is skipped
only when the loopback LM Studio endpoint cannot be reached; a reachable server
that returns empty/malformed output is a real failure and is surfaced, not hidden.
Run from the repo root so it reads the project's opencode.json.
"""
import os

import pytest

from candidatelab.policyaudit import PolicyGemma, RuntimeConfigError

CONFIG = os.path.join(os.path.dirname(__file__), os.pardir, "opencode.json")


def test_live_gemma_extracts_a_stated_length():
    try:
        agent = PolicyGemma(CONFIG)
    except RuntimeConfigError as exc:
        pytest.skip(f"runtime model not configured: {exc}")
    payload = {
        "context": "account",
        "known_facts": {}, "asked_topics": [], "unknown_topics": [],
        "allowed_topics": ["intended_use", "min_length", "max_length",
                           "character_requirements", "allow_spaces",
                           "unicode_policy", "normalization"],
        "available_topics": ["max_length", "character_requirements"],
        "answered_topic": None,
        "new_text": "The minimum password length is 14 characters.",
    }
    try:
        result = agent.extract(payload)
    except ValueError as exc:
        if "could not reach" in str(exc):
            pytest.skip(f"local model endpoint unreachable: {exc}")
        raise
    assert set(result) == {"facts", "unknown_facts", "next_topic"}
    assert any(fact.get("key") == "min_length" and "14" in fact.get("value", "")
               for fact in result["facts"]), result
