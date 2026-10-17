"""Early retro TUI shell wired to the shared configuration and planning services.

Inputs edits one Config (reusing its validation, never a second schema), Review
shows the same plan the CLI renders, and Output previews a small, separately
bounded dry run. Generation and later integrations stay unavailable here.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from dataclasses import dataclass, replace

from .config import Config, parse_wordlist_paths
from .engine import run as engine_run
from .planner import plan

# Retro theme tokens mirror the CLI presentation palette: charcoal ground,
# phosphor green, restrained crimson borders, amber notices.
THEME = {
    "ground": "#101210",
    "phosphor": "#33ff66",
    "phosphor_dim": "#1f9f3f",
    "crimson": "#b2312a",
    "amber": "#f4b740",
    "text": "#d7f7d7",
}

SCREEN_NAMES = ("Output", "Inputs", "Review", "Generate", "Files")

# One editable field per Config concept the early shell exposes. Fictional,
# offline defaults keep Review and Output meaningful before any editing.
FIELDS = (
    ("mode", "Mode (passwords/usernames/pairs)", "passwords"),
    ("preset", "Preset (generic/wpa2)", "generic"),
    ("output_format", "Output format (text/jsonl)", "text"),
    ("output", "Output destination", "candidates.txt"),
    ("seeds", "Seed words (comma separated)", "autumn,summer,demo"),
    ("inputs", "Wordlist paths (comma separated)", ""),
    ("languages", "Languages (comma separated)", "en"),
    ("min_length", "Minimum length", "1"),
    ("max_length", "Maximum length", "40"),
    ("max_candidates", "Max candidates", "1000"),
    ("max_attempts", "Max attempts", "100000"),
    ("max_seconds", "Max seconds (blank = none)", ""),
    ("case", "Case variants (lower/upper/capitalized)", "capitalized"),
    ("suffixes", "Suffixes (comma separated)", "!,1"),
    ("leet", "Leet substitution (yes/no)", "no"),
)

DEFAULT_LEET = {"a": ("@",), "e": ("3",), "i": ("1",), "o": ("0",), "s": ("$",)}

SAMPLE_LIMIT = 8

CLI_HINT = (
    "Textual is not available or no interactive terminal was detected.\n"
    "The TUI is an optional extra; install it with:\n"
    "    pip install 'candidatelab[ui]'\n"
    "Meanwhile every feature is reachable from the CLI, for example:\n"
    "    candidatelab plan --human\n"
    "    candidatelab generate --output candidates.txt\n"
    "    candidatelab init"
)


def _split(value: str) -> list[str]:
    """Split a comma- or newline-separated field into trimmed, nonempty terms."""
    return [part.strip() for part in str(value).replace("\n", ",").split(",") if part.strip()]


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in ("yes", "y", "true", "on", "1")


def config_from_fields(values: dict) -> Config:
    """Build a Config from raw TUI field strings, reusing Config validation.

    Returns the validated Config. Raises ValueError (from Config/transform
    validation or int/float parsing) when the field values are inconsistent.
    """
    data: dict = {}
    for key in ("mode", "preset", "output_format", "output"):
        text = str(values.get(key, "")).strip()
        if text:
            data[key] = text
    if str(values.get("seeds", "")).strip():
        data["seeds"] = _split(values["seeds"])
    if str(values.get("inputs", "")).strip():
        data["inputs"] = list(parse_wordlist_paths(values["inputs"]))
    if str(values.get("languages", "")).strip():
        data["languages"] = _split(values["languages"])
    for key in ("min_length", "max_length", "max_candidates", "max_attempts"):
        text = str(values.get(key, "")).strip()
        if text:
            try:
                data[key] = int(text)
            except ValueError:
                raise ValueError(f"{key} must be an integer")
    seconds = str(values.get("max_seconds", "")).strip()
    if seconds:
        try:
            data["max_seconds"] = float(seconds)
        except ValueError:
            raise ValueError("max_seconds must be a number or blank")
    transforms: dict = {}
    if str(values.get("case", "")).strip():
        transforms["case"] = _split(values["case"])
    if str(values.get("suffixes", "")).strip():
        transforms["suffixes"] = _split(values["suffixes"])
    if _truthy(values.get("leet", "")):
        transforms["leet"] = {key: list(choices) for key, choices in DEFAULT_LEET.items()}
    if transforms:
        data["transforms"] = transforms
    return Config.from_dict(data)


def plan_for(config: Config) -> dict:
    """Return the same bounded plan description the CLI renders for ``config``."""
    return plan(config)


def sample_candidates(config: Config, limit: int = SAMPLE_LIMIT):
    """Run a separate, tightly bounded dry run into a throwaway file and return
    (lines, Result). Never reads or consumes the real generation output."""
    with tempfile.TemporaryDirectory() as directory:
        destination = os.path.join(directory, "sample.out")
        probe = replace(
            config,
            output=destination,
            max_candidates=min(config.max_candidates, limit),
            max_attempts=min(config.max_attempts, max(limit * 100, 1000)),
            max_output_bytes=min(config.max_output_bytes, 1 << 16),
            max_seconds=config.max_seconds if config.max_seconds is not None else 2.0,
        )
        result = engine_run(probe)
        with open(destination, encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for _, line in zip(range(limit), handle)]
    return lines, result


@dataclass(frozen=True)
class ProgressEvent:
    """A coalesced, immutable snapshot handed from the engine thread to the UI.

    ``phase`` is "running", "done", "cancelled" or "failed". Running events carry
    live counters; terminal events add the stop reason, byte count and digest.
    ``partial`` marks output that stopped early (cancellation or a budget stop).
    """
    phase: str
    accepted: int = 0
    attempted: int = 0
    filtered: int = 0
    duplicates: int = 0
    quota_skipped: int = 0
    output_bytes: int = 0
    stop_reason: str = ""
    sha256: str = ""
    output: str = ""
    partial: bool = False
    error: str = ""

    @classmethod
    def running(cls, result) -> "ProgressEvent":
        return cls("running", accepted=result.accepted, attempted=result.attempted,
                   filtered=result.filtered, duplicates=result.duplicates,
                   quota_skipped=result.quota_skipped, output_bytes=result.output_bytes)

    @classmethod
    def _final(cls, phase, result, output, partial) -> "ProgressEvent":
        return cls(phase, accepted=result.accepted, attempted=result.attempted,
                   filtered=result.filtered, duplicates=result.duplicates,
                   quota_skipped=result.quota_skipped, output_bytes=result.output_bytes,
                   stop_reason=result.stop_reason, sha256=result.sha256,
                   output=output, partial=partial)

    @classmethod
    def done(cls, result, output) -> "ProgressEvent":
        partial = result.stop_reason not in ("input_exhausted", "candidate_budget")
        return cls._final("done", result, output, partial)

    @classmethod
    def cancelled(cls, result, output) -> "ProgressEvent":
        return cls._final("cancelled", result, output, True)

    @classmethod
    def failed(cls, error, output) -> "ProgressEvent":
        return cls("failed", output=output, partial=True, error=str(error))

    def describe(self) -> str:
        if self.phase == "running":
            return (f"Running... accepted={self.accepted} attempted={self.attempted} "
                    f"filtered={self.filtered} dup={self.duplicates} bytes={self.output_bytes}")
        if self.phase == "failed":
            return f"FAILED: {self.error}\nOutput may be absent or partial at {self.output}."
        header = "CANCELLED" if self.phase == "cancelled" else "DONE"
        partial = " (partial output)" if self.partial else ""
        return (f"{header}{partial}\n"
                f"accepted={self.accepted} attempted={self.attempted} "
                f"filtered={self.filtered} dup={self.duplicates} quota_skipped={self.quota_skipped}\n"
                f"bytes={self.output_bytes} stop={self.stop_reason}\n"
                f"output={self.output}\nsha256={self.sha256}")


def export(config: Config, emit, should_cancel):
    """Run one bounded export, emitting coalesced ProgressEvents via ``emit``.

    ``emit`` receives running snapshots then exactly one terminal event
    (done/cancelled/failed). ``should_cancel`` is a callable polled cooperatively
    by the engine. Returns the engine Result, or None when the run failed.
    Intended to run on a worker thread so the engine stays off the UI loop.
    """
    def progress(result):
        emit(ProgressEvent.running(result))
    try:
        result = engine_run(config, cancelled=should_cancel, progress=progress)
    except (OSError, ValueError) as exc:
        emit(ProgressEvent.failed(exc, config.output))
        return None
    if result.stop_reason == "cancelled" or should_cancel():
        emit(ProgressEvent.cancelled(result, config.output))
    else:
        emit(ProgressEvent.done(result, config.output))
    return result


def _review_text(config: Config) -> str:
    description = plan_for(config)
    bounds = description["effective_password_lengths"]
    length = f"{bounds['minimum']}-{bounds['maximum']}" if bounds else "not applicable"
    upper = description["raw_expansion_upper"]
    return (
        f"Mode / preset   {description['mode']} / {description['preset']}\n"
        f"Output          {description['output']} ({description['output_format']})\n"
        f"Password chars  {length}\n"
        f"Raw upper bound {upper if upper is not None else 'unknown; wordlists not scanned'}\n"
        f"Emitted upper   {description['emitted_upper']}\n"
        f"Attempt cap     {description['budgets']['attempts']}\n"
        f"Mutation order  {', '.join(description['custom_priority_tiers'])}\n"
        f"{description['estimate_notes']}"
    )


def _build_app():
    """Construct the Textual app class lazily so importing this module stays cheap
    and does not require Textual to be installed."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import (
        Footer,
        Header,
        Input,
        Label,
        Static,
        TabbedContent,
        TabPane,
    )

    css = """
    Screen { background: %(ground)s; color: %(text)s; }
    Header { background: %(ground)s; color: %(phosphor)s; text-style: bold; }
    Footer { background: %(ground)s; color: %(phosphor_dim)s; }
    TabbedContent { border: round %(crimson)s; }
    Tabs { background: %(ground)s; }
    Tab { color: %(phosphor_dim)s; }
    Tab.-active { color: %(phosphor)s; text-style: bold; }
    .pane-note { color: %(amber)s; padding: 0 1; }
    .pane-body { padding: 0 1; }
    .field-label { color: %(phosphor_dim)s; padding: 0 1; }
    Input { background: %(ground)s; border: tall %(phosphor_dim)s; }
    Input:focus { border: tall %(phosphor)s; }
    #notice { color: %(amber)s; padding: 0 1; }
    #help-box { border: round %(crimson)s; background: %(ground)s;
                padding: 1 2; width: 64; height: auto; }
    #help-box .pane-note { color: %(phosphor)s; text-style: bold; }
    """ % THEME

    class HelpScreen(ModalScreen):
        BINDINGS = [Binding("escape,question_mark,f1", "dismiss", "Close")]

        def compose(self) -> ComposeResult:
            with Vertical(id="help-box"):
                yield Static("CANDIDATELAB TUI — EARLY SHELL", classes="pane-note")
                yield Static(
                    "Inputs edits one configuration; Review and Output read it.\n"
                    "Output previews a separate, tightly bounded dry run.\n"
                    "Generation and integrations are not available here.\n\n"
                    "1-5 . jump to a screen\n"
                    "tab / shift+tab . move focus\n"
                    "left / right . change screen\n"
                    "ctrl+r . apply inputs (validate + refresh)\n"
                    "ctrl+s / ctrl+l . save / load configuration\n"
                    "ctrl+g / ctrl+x . start / cancel background export\n"
                    "? or F1 . this help      q . quit",
                    classes="pane-body",
                )

    class CandidateLabTUI(App):
        CSS = css
        TITLE = "CANDIDATELAB // OFFLINE SHELL"
        BINDINGS = [
            Binding("q,ctrl+c", "quit", "Quit"),
            Binding("question_mark,f1", "help", "Help"),
            Binding("ctrl+r", "apply", "Apply"),
            Binding("ctrl+s", "save", "Save"),
            Binding("ctrl+l", "load", "Load"),
            Binding("ctrl+g", "generate", "Generate"),
            Binding("ctrl+x", "cancel_job", "Cancel"),
            Binding("left", "prev_screen", "Prev", show=False),
            Binding("right", "next_screen", "Next", show=False),
            Binding("1", "goto('Output')", "Output", show=False),
            Binding("2", "goto('Inputs')", "Inputs", show=False),
            Binding("3", "goto('Review')", "Review", show=False),
            Binding("4", "goto('Generate')", "Generate", show=False),
            Binding("5", "goto('Files')", "Files", show=False),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.config: Config | None = None
            self._cancel = threading.Event()
            self._busy = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with TabbedContent(initial="tab-inputs", id="screens"):
                with TabPane("Output", id="tab-output"):
                    yield Static("Bounded dry-run sample (separate from real output).", classes="pane-note")
                    yield Static("Apply inputs with ctrl+r to preview candidates.", id="output-body", classes="pane-body")
                with TabPane("Inputs", id="tab-inputs"):
                    yield Static("Edit configuration; ctrl+r validates and refreshes.", classes="pane-note")
                    with VerticalScroll():
                        for key, label, default in FIELDS:
                            yield Label(label, classes="field-label")
                            yield Input(value=default, id=f"f-{key}")
                    yield Static("", id="notice")
                with TabPane("Review", id="tab-review"):
                    yield Static("Effective bounded plan (identical to the CLI).", classes="pane-note")
                    yield Static("Apply inputs with ctrl+r to see the plan.", id="review-body", classes="pane-body")
                with TabPane("Generate", id="tab-generate"):
                    yield Static("Run a bounded export in the background (ctrl+g / ctrl+x).", classes="pane-note")
                    yield Static(
                        "Apply a configuration (ctrl+r), then press ctrl+g to export\n"
                        "to its output file. Candidate bytes are written only to that\n"
                        "file, never through this interface.",
                        id="generate-body", classes="pane-body",
                    )
                with TabPane("Files", id="tab-files"):
                    yield Static("Save or load a CLI-compatible configuration.", classes="pane-note")
                    yield Label("Configuration path", classes="field-label")
                    yield Input(value="candidatelab.json", id="f-configpath")
                    yield Static("ctrl+s saves the applied config; ctrl+l loads one.", id="files-body", classes="pane-body")
            yield Footer()

        def on_mount(self) -> None:
            self.action_apply()

        # --- navigation -------------------------------------------------
        def _tabs(self):
            return self.query_one("#screens", TabbedContent)

        def action_goto(self, name: str) -> None:
            self._tabs().active = f"tab-{name.lower()}"

        def _shift(self, delta: int) -> None:
            tabs = self._tabs()
            ids = [f"tab-{name.lower()}" for name in SCREEN_NAMES]
            tabs.active = ids[(ids.index(tabs.active) + delta) % len(ids)]

        def action_next_screen(self) -> None:
            self._shift(1)

        def action_prev_screen(self) -> None:
            self._shift(-1)

        def action_help(self) -> None:
            self.push_screen(HelpScreen())

        # --- configuration services ------------------------------------
        def _collect(self) -> dict:
            values = {}
            for key, _, _ in FIELDS:
                values[key] = self.query_one(f"#f-{key}", Input).value
            return values

        def _notice(self, message: str) -> None:
            self.query_one("#notice", Static).update(message)

        def action_apply(self) -> None:
            try:
                config = config_from_fields(self._collect())
            except ValueError as exc:
                self._notice(f"Invalid: {exc}")
                return
            self.config = config
            self.query_one("#review-body", Static).update(_review_text(config))
            try:
                lines, result = sample_candidates(config)
            except (OSError, ValueError) as exc:
                self.query_one("#output-body", Static).update(f"Dry run unavailable: {exc}")
                self._notice("Configuration valid; sample unavailable (see Output).")
                return
            body = "\n".join(lines) if lines else "(no candidates for this configuration)"
            self.query_one("#output-body", Static).update(
                f"{body}\n\nstop={result.stop_reason}; accepted={result.accepted}"
            )
            self._notice("Configuration valid; Review and Output updated.")

        def action_save(self) -> None:
            if self.config is None:
                self._notice("Apply a valid configuration before saving.")
                return
            path = self.query_one("#f-configpath", Input).value.strip()
            try:
                self.config.save(path)
            except (OSError, ValueError) as exc:
                self.query_one("#files-body", Static).update(f"Save failed: {exc}")
                return
            self.query_one("#files-body", Static).update(f"Saved configuration to {path}")

        def action_load(self) -> None:
            path = self.query_one("#f-configpath", Input).value.strip()
            try:
                config = Config.load(path)
            except (OSError, ValueError) as exc:
                self.query_one("#files-body", Static).update(f"Load failed: {exc}")
                return
            self.config = config
            self.query_one("#review-body", Static).update(_review_text(config))
            self.query_one("#files-body", Static).update(f"Loaded configuration from {path}")

        # --- background export -----------------------------------------
        def _set_generate(self, message: str) -> None:
            self.query_one("#generate-body", Static).update(message)

        def _apply_event(self, event: ProgressEvent) -> None:
            self._set_generate(event.describe())
            if event.phase != "running":
                self._busy = False

        def _do_export(self) -> None:
            config = self.config
            export(config,
                   lambda event: self.call_from_thread(self._apply_event, event),
                   self._cancel.is_set)

        def action_generate(self) -> None:
            self._tabs().active = "tab-generate"
            if self._busy:
                self._set_generate("A job is already running; press ctrl+x to cancel.")
                return
            if self.config is None:
                self._set_generate("Apply a valid configuration first (ctrl+r).")
                return
            self._cancel.clear()
            self._busy = True
            self._set_generate(f"Starting background export to {self.config.output}...")
            self.run_worker(self._do_export, thread=True, group="export", exclusive=True)

        def action_cancel_job(self) -> None:
            if self._busy:
                self._cancel.set()
                self._set_generate("Cancelling; waiting for the job to stop...")
            else:
                self._set_generate("No job is running.")

    return CandidateLabTUI


def run(stream=None) -> int:
    """Launch the TUI shell. Returns 0 on a clean exit, 1 if Textual is missing
    or no usable terminal exists, after printing the CLI route to ``stream``."""
    stream = stream if stream is not None else sys.stderr
    try:
        app_class = _build_app()
    except ImportError:
        print(CLI_HINT, file=stream)
        return 1
    if not (getattr(sys.stdout, "isatty", lambda: False)()
            and getattr(sys.stdin, "isatty", lambda: False)()):
        print(CLI_HINT, file=stream)
        return 1
    app_class().run()
    return 0
