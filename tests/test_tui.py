"""Headless checks for the early TUI shell and its configuration wiring."""
import asyncio
import io

from candidatelab import tui
from candidatelab.config import Config
from candidatelab.engine import run as engine_run
from candidatelab.planner import plan


def _run(coro):
    return asyncio.run(coro)


def _app():
    return tui._build_app()()


# --- navigation / shell ------------------------------------------------
def test_screen_shell_exposes_the_five_named_screens():
    async def scenario():
        app = _app()
        async with app.run_test(size=(120, 40)):
            tabs = app.query_one("#screens")
            labels = [str(pane._title) for pane in tabs.query("TabPane")]
            assert labels == list(tui.SCREEN_NAMES)
    _run(scenario())


def test_number_keys_jump_between_screens_at_80x24():
    async def scenario():
        app = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("5")
            assert app.query_one("#screens").active == "tab-files"
            await pilot.press("1")
            assert app.query_one("#screens").active == "tab-output"
    _run(scenario())


def test_screen_shift_actions_wrap_around():
    async def scenario():
        app = _app()
        async with app.run_test(size=(80, 24)):
            app.action_goto("Output")
            assert app.query_one("#screens").active == "tab-output"
            app.action_prev_screen()
            assert app.query_one("#screens").active == "tab-files"
            app.action_next_screen()
            assert app.query_one("#screens").active == "tab-output"
    _run(scenario())


def test_focus_is_visible_and_moves_with_tab():
    async def scenario():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("tab")
            assert app.focused is not None
    _run(scenario())


def test_help_opens_and_closes():
    async def scenario():
        app = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("question_mark")
            assert app.screen.__class__.__name__ == "HelpScreen"
            await pilot.press("escape")
            assert app.screen.__class__.__name__ != "HelpScreen"
    _run(scenario())


def test_resize_keeps_the_shell_running():
    async def scenario():
        app = _app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            assert app.query_one("#screens").active == "tab-inputs"
    _run(scenario())


def test_run_without_a_terminal_prints_cli_route_and_returns_one():
    stream = io.StringIO()
    code = tui.run(stream)
    assert code == 1
    assert "candidatelab plan" in stream.getvalue()


# --- configuration services -------------------------------------------
def test_config_from_fields_reuses_config_validation():
    config = tui.config_from_fields({
        "mode": "passwords", "preset": "generic", "seeds": "alpha, beta",
        "max_candidates": "5", "leet": "yes", "suffixes": "!,1",
    })
    assert isinstance(config, Config)
    assert config.seeds == ("alpha", "beta")
    assert config.max_candidates == 5
    assert config.transforms.leet and config.transforms.suffixes == ("!", "1")


def test_config_from_fields_surfaces_invalid_values():
    try:
        tui.config_from_fields({"seeds": "alpha", "max_candidates": "not-a-number"})
    except ValueError as exc:
        assert "max_candidates" in str(exc)
    else:
        raise AssertionError("expected ValueError for a non-integer budget")


def test_sample_candidates_is_a_separate_bounded_dry_run(tmp_path):
    real_output = tmp_path / "real.txt"
    config = tui.config_from_fields({
        "seeds": "autumn,summer", "output": str(real_output), "max_candidates": "1000",
    })
    lines, result = tui.sample_candidates(config, limit=3)
    assert 0 < len(lines) <= 3
    assert result.accepted <= 3
    # The real destination is never written by the preview dry run.
    assert not real_output.exists()


def test_tui_plan_matches_cli_plan_for_the_same_config():
    fields = {"mode": "passwords", "preset": "generic", "seeds": "autumn,summer",
              "max_candidates": "100", "case": "capitalized"}
    config = tui.config_from_fields(fields)
    assert tui.plan_for(config) == plan(config)


def test_keyboard_configuration_round_trip(tmp_path):
    destination = tmp_path / "roundtrip.json"

    async def scenario():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            seeds = app.query_one("#f-seeds")
            seeds.focus()
            seeds.value = ""
            await pilot.pause()
            await pilot.press("w", "i", "n", "t", "e", "r")
            app.query_one("#f-configpath").value = str(destination)
            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            return app.config

    built = _run(scenario())
    assert destination.exists()
    loaded = Config.load(destination)
    assert loaded.seeds == ("winter",)

    # Effective plan is identical after a save/load round trip, except for the
    # output path, which Config.load intentionally resolves beside the file.
    def _without_output(config):
        return {key: value for key, value in plan(config).items() if key != "output"}

    assert _without_output(loaded) == _without_output(built)


# --- background export job --------------------------------------------
def test_export_emits_running_then_a_single_done_event(tmp_path):
    config = tui.config_from_fields({
        "seeds": "autumn,summer,winter", "case": "capitalized", "suffixes": "!,1",
        "output": str(tmp_path / "out.txt"), "max_candidates": "50",
    })
    events = []
    result = tui.export(config, events.append, lambda: False)
    terminal = [event for event in events if event.phase != "running"]
    assert len(terminal) == 1 and terminal[0].phase == "done"
    assert result is not None and terminal[0].accepted == result.accepted
    assert not terminal[0].partial


def test_export_cancellation_marks_partial_and_stops(tmp_path):
    config = tui.config_from_fields({
        "seeds": "autumn,summer,winter", "case": "capitalized", "suffixes": "!,1",
        "output": str(tmp_path / "cancel.txt"), "max_candidates": "100000",
    })
    result = tui.export(config, lambda event: None, lambda: True)
    assert result is not None and result.stop_reason == "cancelled"
    assert result.accepted == 0


def test_export_failure_emits_failed_event_for_existing_output(tmp_path):
    destination = tmp_path / "exists.txt"
    destination.write_text("already here\n")
    config = tui.config_from_fields({"seeds": "autumn", "output": str(destination)})
    events = []
    result = tui.export(config, events.append, lambda: False)
    assert result is None
    assert events and events[-1].phase == "failed" and events[-1].partial


def test_cli_and_tui_produce_identical_output_bytes(tmp_path):
    fields = {"seeds": "autumn,summer,winter", "case": "capitalized",
              "suffixes": "!,1", "max_candidates": "40"}
    cli_path = tmp_path / "cli.txt"
    tui_path = tmp_path / "tui.txt"
    cli_config = tui.config_from_fields({**fields, "output": str(cli_path)})
    tui_config = tui.config_from_fields({**fields, "output": str(tui_path)})
    engine_run(cli_config)
    tui.export(tui_config, lambda event: None, lambda: False)
    assert cli_path.read_bytes() == tui_path.read_bytes()


def test_headless_generate_runs_and_clears_busy_state(tmp_path):
    destination = tmp_path / "generated.txt"

    async def scenario():
        app = _app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#f-output").value = str(destination)
            app.query_one("#f-max_candidates").value = "20"
            app.action_apply()
            app.action_generate()
            for _ in range(100):
                if not app._busy:
                    break
                await pilot.pause(0.05)
            assert not app._busy
            body = str(app.query_one("#generate-body").render())
            assert "DONE" in body and "sha256=" in body
    _run(scenario())
    assert destination.exists()
