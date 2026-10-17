#!/usr/bin/env python3
"""CandidateLab — single entrypoint.

Start it with:

    python main.py

This launches the interactive menu. The defensive password-policy audit MVP is
wired and working; other roadmap features are shown as "(not yet implemented)".
The menu drives the same services as the `candidatelab` CLI, so everything here
is also reachable non-interactively (see README).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Prefer the in-repo source over any installed copy, so `python main.py` always
# runs the current code without a reinstall.
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))

from candidatelab import __version__  # noqa: E402
from candidatelab.logo import print_banner  # noqa: E402
from candidatelab.policyaudit import (REQUIRED_MODEL, RuntimeConfigError,  # noqa: E402
                                      resolve_runtime_model)
from candidatelab.presentation import Presentation  # noqa: E402
from candidatelab import wordlistgen  # noqa: E402

RUNTIME_CONFIG = ROOT / "opencode.json"

# (key, label, status) — status None means wired/working; a string marks it deferred.
MENU = [
    ("pair", "Account Username & Password", "not yet implemented"),
    ("username", "Account Username only", "not yet implemented"),
    ("password", "Account Password only", None),
    ("wifi", "Wi-Fi passphrase", None),
    ("osint", "Account & entity discovery (OSINT tools)", "not yet implemented"),
    ("resume", "Resume previous checkpoint", None),
]


# Working menu keys map to the synthetic policy-audit contexts.
_CONTEXT = {"password": "account", "wifi": "wifi"}


def _model_banner(display: Presentation) -> bool:
    try:
        base, model = resolve_runtime_model(str(RUNTIME_CONFIG))
    except RuntimeConfigError as exc:
        display.error(f"local model not configured: {exc}")
        return False
    display.text(f"Runtime model: {model} @ {base}", style="retro.notice")
    return True


def _report_model_failure(display: Presentation, exc: Exception) -> None:
    display.error(str(exc))
    display.text("Nothing was written. Check that LM Studio is serving "
                 f"{REQUIRED_MODEL}, then try again.", style="retro.notice")


def _generate(display: Presentation, key: str) -> None:
    context = _CONTEXT[key]
    label = "account password" if context == "account" else "Wi-Fi passphrase"
    display.text(f"Custom {label} wordlist generation.", style="retro.title")
    if not _model_banner(display):
        return
    default_out = f"{context}_wordlist.txt"
    try:
        agent = wordlistgen.TargetGemma(str(RUNTIME_CONFIG))
        wordlistgen.generate(context, default_out, agent, display.prompt, display.message)
    except RuntimeConfigError as exc:
        display.error(f"local model not configured: {exc}")
    except ValueError as exc:
        _report_model_failure(display, exc)
    except KeyboardInterrupt:
        display.text("\nCancelled; no wordlist was written.", style="retro.notice")


def _resume(display: Presentation) -> None:
    display.text("Resume a saved wordlist session.", style="retro.title")
    sessions = sorted(str(p) for p in Path.cwd().glob("*.session.json"))
    if sessions:
        display.text("Session files found in this folder:", style="retro.notice")
        for path in sessions:
            display.text(f"  {path}", style="retro.text")
    default = sessions[0] if sessions else ""
    prompt = f"Session file to resume [{default}]:" if default else "Session file to resume:"
    chosen = display.prompt(prompt).strip() or default
    if not chosen:
        display.error("no session file given")
        return
    if not _model_banner(display):
        return
    try:
        agent = wordlistgen.TargetGemma(str(RUNTIME_CONFIG))
        wordlistgen.resume(chosen, agent, display.prompt, display.message)
    except FileNotFoundError:
        display.error(f"no such session file: {chosen}")
    except RuntimeConfigError as exc:
        display.error(f"local model not configured: {exc}")
    except ValueError as exc:
        _report_model_failure(display, exc)
    except KeyboardInterrupt:
        display.text("\nCancelled; the session file was left unchanged.", style="retro.notice")


def _menu(display: Presentation) -> None:
    while True:
        display.text("")
        display.text("Select an option:", style="retro.title")
        for index, (_, label, status) in enumerate(MENU, start=1):
            suffix = "" if status is None else f"  — ({status})"
            display.text(f"  {index}. {label}{suffix}",
                         style="retro.key" if status is None else "retro.notice")
        display.text("  q. Quit", style="retro.key")
        choice = display.prompt("Choice:").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            display.text("Bye.", style="retro.title")
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            display.error("choose a listed number or q")
            continue
        key, _, status = MENU[int(choice) - 1]
        if status is not None:
            display.text(f"That option is ({status}).", style="retro.notice")
            continue
        if key == "resume":
            _resume(display)
        else:
            _generate(display, key)


def main(argv=None) -> int:
    display = Presentation(sys.stderr)
    animate = sys.stdout.isatty()
    print_banner(animate=animate)
    display.text(f"CandidateLab v{__version__} — custom wordlist generator (local Gemma)",
                 style="retro.title")
    display.text("Authorized testing only. Local & offline; produces a candidate wordlist, "
                 "never attempts a login.", style="retro.notice")
    if not sys.stdin.isatty():
        display.error("the interactive menu needs a terminal; "
                      "use the `candidatelab` CLI for non-interactive runs")
        return 1
    try:
        _menu(display)
    except (KeyboardInterrupt, EOFError):
        display.text("\nInterrupted.", style="retro.notice")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
