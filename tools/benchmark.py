"""Reproducible, offline performance harness for the sequential engine.

Runs a fixed fictional configuration through engine.run and reports attempted
and unique candidates per second, wall time, bytes written and peak memory,
tagged with CPU architecture, OS and Python version. This is a baseline for the
future parallel phases (020, 021); it sets no hardware-independent speed target.

Usage:
    python tools/benchmark.py [--backend sqlite|memory] [--max-candidates N]
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from candidatelab.config import Config  # noqa: E402
from candidatelab.engine import run  # noqa: E402

# Deterministic fictional vocabulary; transforms fan each seed out into variants.
SEEDS = tuple(f"seed{index:03d}" for index in range(200))
TRANSFORMS = {
    "case": ["lower", "upper", "capitalized"],
    "suffixes": ["!", "1", "123", "2024"],
    "max_variants": 64,
}


def measure(backend: str, max_candidates: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        output = str(Path(directory) / "bench.txt")
        config = Config(
            seeds=SEEDS, output=output, max_candidates=max_candidates,
            max_attempts=max_candidates * 50 + 100000,
            transforms=dict(TRANSFORMS),
        )
        tracemalloc.start()
        started = time.perf_counter()
        result = run(config, dedupe_backend=backend)
        wall = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    rate = (lambda count: round(count / wall, 1) if wall > 0 else None)
    return {
        "backend": backend,
        "wall_seconds": round(wall, 4),
        "attempted": result.attempted,
        "unique_accepted": result.accepted,
        "duplicates": result.duplicates,
        "filtered": result.filtered,
        "bytes_written": result.output_bytes,
        "attempted_per_second": rate(result.attempted),
        "unique_per_second": rate(result.accepted),
        "peak_tracemalloc_bytes": peak,
        "stop_reason": result.stop_reason,
        "cpu_arch": platform.machine(),
        "os": f"{platform.system()} {platform.release()}",
        "python_version": platform.python_version(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CandidateLab engine benchmark")
    parser.add_argument("--backend", choices=("sqlite", "memory"), default="sqlite")
    parser.add_argument("--max-candidates", type=int, default=20000)
    args = parser.parse_args(argv)
    print(json.dumps(measure(args.backend, args.max_candidates), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
