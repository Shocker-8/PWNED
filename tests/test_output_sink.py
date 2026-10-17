"""Engine output-sink and dedupe-backend checks for phase 016."""
import pathlib

import pytest

import candidatelab.engine as engine
from candidatelab.config import Config
from candidatelab.dedupe import DedupeError
from candidatelab.engine import run


def _passwords(output, **extra):
    return Config(seeds=("admin", "ADMIN"), output=str(output),
                  transforms={"case": ["lower"]}, max_candidates=100, **extra)


@pytest.mark.parametrize("fmt", ["text", "jsonl"])
def test_in_memory_and_sqlite_backends_produce_identical_bytes(tmp_path, fmt):
    sqlite_out = tmp_path / f"sqlite.{fmt}"
    memory_out = tmp_path / f"memory.{fmt}"
    run(_passwords(sqlite_out, output_format=fmt), dedupe_backend="sqlite")
    run(_passwords(memory_out, output_format=fmt), dedupe_backend="memory")
    assert sqlite_out.read_bytes() == memory_out.read_bytes()


def test_colliding_transform_paths_are_deduplicated(tmp_path):
    output = tmp_path / "collide.txt"
    # "admin" original and lower("ADMIN") collide on the same candidate bytes.
    result = run(_passwords(output), dedupe_backend="sqlite")
    lines = output.read_text().splitlines()
    assert lines == list(dict.fromkeys(lines))  # no duplicate records
    assert result.duplicates > 0


def test_existing_output_fails_cleanly_without_wrapping_as_success(tmp_path):
    output = tmp_path / "exists.txt"
    output.write_text("prior\n")
    with pytest.raises(FileExistsError):
        run(_passwords(output))


def test_dedupe_backend_error_yields_explicitly_partial_output(tmp_path, monkeypatch):
    class FaultyStore:
        def __init__(self): self.calls = 0
        def contains(self, key):
            self.calls += 1
            if self.calls >= 3:
                raise DedupeError("simulated database failure")
            return False
        def add(self, key): pass
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    monkeypatch.setattr(engine, "make_dedupe", lambda *a, **k: FaultyStore())
    with pytest.raises(ValueError, match="partial output"):
        run(_passwords(tmp_path / "boom.txt"))


def test_broken_pipe_stops_cleanly_without_traceback(tmp_path, monkeypatch):
    real_open = pathlib.Path.open

    class BrokenWriter:
        def __init__(self, handle): self._handle = handle
        def write(self, data): raise BrokenPipeError("downstream closed")
        def __enter__(self): return self
        def __exit__(self, *exc): self._handle.close(); return False

    def fake_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        return BrokenWriter(handle) if ("x" in mode or "w" in mode) else handle

    monkeypatch.setattr(pathlib.Path, "open", fake_open)
    result = run(_passwords(tmp_path / "pipe.txt"))
    assert result.stop_reason == "output_closed"
    assert result.accepted == 0


def test_benchmark_harness_reports_expected_fields():
    import sys
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools import benchmark
    report = benchmark.measure("memory", max_candidates=50)
    for field in ("backend", "wall_seconds", "attempted", "unique_accepted",
                  "bytes_written", "unique_per_second", "peak_tracemalloc_bytes",
                  "cpu_arch", "os", "python_version"):
        assert field in report
    assert report["unique_accepted"] <= 50
