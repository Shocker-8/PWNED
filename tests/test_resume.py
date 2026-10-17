"""Resume-by-replay checks for phase 018.

Partial state is built deterministically: generation order is independent of the
candidate cap, so the output of max_candidates=j is exactly the first j records
(and leading bytes) of a larger run. That lets us construct an exact, valid
checkpoint for a half-finished run without relying on cancellation timing.
"""
import pytest

from candidatelab.checkpoint import (Checkpoint, ResumeError, config_fingerprint,
                                     fingerprint_sources, write_checkpoint)
from candidatelab.config import Config
from candidatelab.engine import run

FULL = 100
PREFIX = 3


def _wordlist(tmp_path):
    path = tmp_path / "w.txt"
    path.write_text("\n".join(f"word{index}" for index in range(8)) + "\n")
    return path


def _resume_config(tmp_path, wl, name="partial.txt"):
    return Config(input=str(wl), output=str(tmp_path / name), max_candidates=FULL)


def _build_partial(tmp_path, wl):
    """Create a partial output + matching checkpoint for the FULL-cap config."""
    config = _resume_config(tmp_path, wl)
    # Deterministic first-PREFIX-records prefix and its digest/offset.
    prefix = tmp_path / "prefix.txt"
    pr = run(Config(input=str(wl), output=str(prefix), max_candidates=PREFIX))
    out = tmp_path / "partial.txt"
    out.write_bytes(prefix.read_bytes())
    ckpt = tmp_path / "partial.ckpt"
    write_checkpoint(ckpt, Checkpoint(
        config_fingerprint=config_fingerprint(config),
        sources=fingerprint_sources([str(wl)]),
        emitted_count=pr.accepted, output_offset=pr.output_bytes, prefix_digest=pr.sha256))
    return config, out, ckpt, pr.accepted


def test_resume_reproduces_a_full_uninterrupted_run(tmp_path):
    wl = _wordlist(tmp_path)
    full = run(Config(input=str(wl), output=str(tmp_path / "full.txt"), max_candidates=FULL))
    full_bytes = (tmp_path / "full.txt").read_bytes()
    config, out, ckpt, partial_count = _build_partial(tmp_path, wl)
    assert 0 < partial_count < full.accepted

    resumed = run(config, resume_from=str(ckpt))
    assert out.read_bytes() == full_bytes
    assert resumed.accepted == full.accepted


def test_resume_truncates_uncommitted_trailing_bytes(tmp_path):
    wl = _wordlist(tmp_path)
    run(Config(input=str(wl), output=str(tmp_path / "full.txt"), max_candidates=FULL))
    full_bytes = (tmp_path / "full.txt").read_bytes()
    config, out, ckpt, _ = _build_partial(tmp_path, wl)
    with out.open("ab") as handle:
        handle.write(b"garbage-not-committed\n")

    run(config, resume_from=str(ckpt))
    assert out.read_bytes() == full_bytes


def test_resume_rejects_config_mismatch_before_touching_output(tmp_path):
    wl = _wordlist(tmp_path)
    config, out, ckpt, _ = _build_partial(tmp_path, wl)
    before = out.read_bytes()
    mismatched = Config(input=str(wl), output=str(out), max_candidates=FULL // 2)
    with pytest.raises(ResumeError, match="configuration fingerprint"):
        run(mismatched, resume_from=str(ckpt))
    assert out.read_bytes() == before


def test_resume_rejects_tampered_output_prefix(tmp_path):
    wl = _wordlist(tmp_path)
    config, out, ckpt, _ = _build_partial(tmp_path, wl)
    data = bytearray(out.read_bytes())
    data[0] ^= 0xFF
    out.write_bytes(data)
    with pytest.raises(ResumeError, match="prefix digest"):
        run(config, resume_from=str(ckpt))


def test_resume_rejects_changed_input_source(tmp_path):
    wl = _wordlist(tmp_path)
    config, out, ckpt, _ = _build_partial(tmp_path, wl)
    wl.write_text("completely\ndifferent\nwords\n")
    with pytest.raises(ResumeError, match="source fingerprints"):
        run(config, resume_from=str(ckpt))
