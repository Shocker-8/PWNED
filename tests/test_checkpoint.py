"""Focused checks for versioned resume checkpoints (phase 017)."""
import hashlib
import json

import pytest

from candidatelab.checkpoint import (CHECKPOINT_VERSION, Checkpoint,
                                     SourceFingerprint, config_fingerprint,
                                     fingerprint_sources, write_checkpoint)
from candidatelab.config import Config
from candidatelab.engine import run


def _wordlist(tmp_path, name="w.txt", body="b\na\nb\nc\na\n"):
    path = tmp_path / name
    path.write_text(body)
    return path


def test_config_fingerprint_is_stable_and_sensitive(tmp_path):
    wl = _wordlist(tmp_path)
    a = config_fingerprint(Config(input=str(wl), output=str(tmp_path / "o1.txt")))
    b = config_fingerprint(Config(input=str(wl), output=str(tmp_path / "o1.txt")))
    c = config_fingerprint(Config(input=str(wl), output=str(tmp_path / "o1.txt"), max_candidates=5))
    assert a == b and a != c


def test_fingerprint_sources_detects_content_change(tmp_path):
    wl = _wordlist(tmp_path)
    first = fingerprint_sources([str(wl)])
    wl.write_text("different\n")
    second = fingerprint_sources([str(wl)])
    assert first[0].sha256 != second[0].sha256
    assert isinstance(first[0], SourceFingerprint)


def test_checkpoint_round_trips_through_json():
    original = Checkpoint(
        config_fingerprint="cfg", sources=(SourceFingerprint("w", 3, 1, "dig"),),
        emitted_count=2, output_offset=10, prefix_digest="px", attempted=4)
    restored = Checkpoint.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original


def test_from_dict_rejects_wrong_version_and_unknown_fields():
    good = Checkpoint(config_fingerprint="c", sources=(), emitted_count=0,
                      output_offset=0, prefix_digest="d").to_dict()
    bad_version = {**good, "version": CHECKPOINT_VERSION + 1}
    with pytest.raises(ValueError, match="version"):
        Checkpoint.from_dict(bad_version)
    with pytest.raises(ValueError, match="unknown"):
        Checkpoint.from_dict({**good, "surprise": 1})


def test_write_checkpoint_is_atomic_and_leaves_no_temp(tmp_path):
    path = tmp_path / "run.ckpt"
    checkpoint = Checkpoint(config_fingerprint="c", sources=(), emitted_count=1,
                            output_offset=2, prefix_digest="d")
    write_checkpoint(path, checkpoint)
    assert Checkpoint.from_dict(json.loads(path.read_text())) == checkpoint
    assert not (tmp_path / "run.ckpt.tmp").exists()


def test_engine_writes_a_checkpoint_matching_the_committed_prefix(tmp_path):
    wl = _wordlist(tmp_path)
    output = tmp_path / "out.txt"
    ckpt = tmp_path / "out.ckpt"
    result = run(Config(input=str(wl), output=str(output), max_candidates=100),
                 checkpoint_path=str(ckpt), checkpoint_every=1)
    checkpoint = Checkpoint.from_dict(json.loads(ckpt.read_text()))
    data = output.read_bytes()
    assert checkpoint.emitted_count == result.accepted
    assert checkpoint.output_offset == len(data) == result.output_bytes
    assert checkpoint.prefix_digest == hashlib.sha256(data).hexdigest() == result.sha256
    assert checkpoint.config_fingerprint and checkpoint.sources
