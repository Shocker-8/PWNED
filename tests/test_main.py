"""Checks for the main.py launcher menu (no live model involved)."""
import importlib.util
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), os.pardir)


def _load_main():
    spec = importlib.util.spec_from_file_location("candidatelab_main", os.path.join(ROOT, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDisplay:
    def __init__(self, answers):
        self.answers = list(answers)
        self.lines = []

    def text(self, value, *, style=None):
        self.lines.append(str(value))

    message = text

    def error(self, message):
        self.lines.append(f"ERROR: {message}")

    def prompt(self, question):
        self.lines.append(question)
        return self.answers.pop(0)


def test_menu_lists_working_and_deferred_items():
    main = _load_main()
    working = [row for row in main.MENU if row[2] is None]
    deferred = [row for row in main.MENU if row[2] is not None]
    assert {row[0] for row in working} == {"password", "wifi", "resume"}
    assert {row[0] for row in deferred} == {"pair", "username", "osint"}
    assert all("not yet implemented" in row[2] for row in deferred)


def test_menu_reports_not_yet_implemented_then_quits():
    main = _load_main()
    display = FakeDisplay(answers=["5", "q"])  # OSINT (deferred) then quit
    main._menu(display)
    assert any("not yet implemented" in line for line in display.lines)
    assert display.lines[-1] == "Bye."


def test_menu_rejects_invalid_choice():
    main = _load_main()
    display = FakeDisplay(answers=["zzz", "q"])
    main._menu(display)
    assert any("choose a listed number" in line for line in display.lines)


def test_generate_reports_missing_runtime_config_without_calling_model(tmp_path, monkeypatch):
    main = _load_main()
    monkeypatch.setattr(main, "RUNTIME_CONFIG", tmp_path / "absent.json")
    display = FakeDisplay(answers=[])  # no prompts reached: resolver fails first
    main._generate(display, "password")  # menu key for the account-password wordlist
    assert any("not configured" in line for line in display.lines)
