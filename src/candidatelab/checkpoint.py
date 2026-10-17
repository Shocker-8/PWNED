"""Versioned resume checkpoints for the deterministic sequential engine.

A checkpoint records exactly what a later resume (phase 018) must validate before
appending to a partial regular-file output: canonical config and source
fingerprints, the software and transform versions, an optional accepted-LLM
snapshot digest, the emitted record count, the durable output byte offset, the
digest of the committed output prefix and the budget consumed so far.

Durability limits: write_checkpoint flushes and fsyncs the output, then atomically
replaces the checkpoint with os.replace, so the checkpoint never names bytes the
output has not durably committed. fsync durability still depends on the OS and
filesystem; power loss between checkpoints can lose up to the records generated
since the last one. Resume is only defined for regular files, not pipes.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import __version__

class ResumeError(ValueError):
    """Raised when a checkpoint, its inputs or the committed output prefix do not
    match, before any existing output is modified."""


CHECKPOINT_VERSION = 1
# Bump when transform/mutation semantics change so stale checkpoints are refused.
TRANSFORM_VERSION = 1
_CHUNK = 1 << 20


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def from_dict(cls, data):
        return cls(**{name: data[name] for name in (f.name for f in fields(cls))})


@dataclass(frozen=True)
class Checkpoint:
    config_fingerprint: str
    sources: tuple[SourceFingerprint, ...]
    emitted_count: int
    output_offset: int
    prefix_digest: str
    attempted: int = 0
    duplicates: int = 0
    filtered: int = 0
    quota_skipped: int = 0
    input_records: int = 0
    llm_snapshot_digest: str | None = None
    version: int = CHECKPOINT_VERSION
    software_version: str = __version__
    transform_version: int = TRANSFORM_VERSION

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sources"] = [asdict(source) for source in self.sources]
        return data

    @classmethod
    def from_dict(cls, data) -> "Checkpoint":
        if not isinstance(data, dict):
            raise ValueError("checkpoint must be a JSON object")
        if data.get("version") != CHECKPOINT_VERSION:
            raise ValueError(f"unsupported checkpoint version: {data.get('version')!r}")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown checkpoint fields: {', '.join(sorted(unknown))}")
        values = dict(data)
        values["sources"] = tuple(SourceFingerprint.from_dict(item) for item in data.get("sources", ()))
        return cls(**values)


def config_fingerprint(config) -> str:
    """Stable SHA-256 over the canonical configuration, independent of key order."""
    canonical = json.dumps(asdict(config), sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fingerprint_sources(paths) -> tuple[SourceFingerprint, ...]:
    """Resolve each mutable input to (path, size, mtime_ns, content digest) before
    a run. Raises OSError if a path cannot be read."""
    results = []
    for path in paths:
        info = os.stat(path)
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
        results.append(SourceFingerprint(str(path), info.st_size, info.st_mtime_ns, digest.hexdigest()))
    return tuple(results)


def load_checkpoint(path) -> Checkpoint:
    """Read and validate a checkpoint file. Raises ValueError on bad content."""
    with Path(path).open(encoding="utf-8") as handle:
        return Checkpoint.from_dict(json.load(handle))


def verify_inputs(config, checkpoint: Checkpoint) -> None:
    """Reject a resume whose configuration or source files no longer match the
    checkpoint. Raises ResumeError before any output is touched."""
    if config_fingerprint(config) != checkpoint.config_fingerprint:
        raise ResumeError("configuration fingerprint does not match the checkpoint")
    try:
        current = fingerprint_sources([source.path for source in checkpoint.sources])
    except OSError as exc:
        raise ResumeError(f"a checkpoint source is unreadable: {exc}") from exc
    if current != checkpoint.sources:
        raise ResumeError("input source fingerprints do not match the checkpoint")


def verify_output_prefix(output_path, checkpoint: Checkpoint) -> None:
    """Confirm the on-disk output holds the checkpoint's committed prefix exactly.
    Raises ResumeError on a short, missing or divergent prefix."""
    try:
        size = os.path.getsize(output_path)
    except OSError as exc:
        raise ResumeError(f"output is not readable: {exc}") from exc
    if size < checkpoint.output_offset:
        raise ResumeError("output is shorter than the checkpoint offset")
    digest = hashlib.sha256()
    remaining = checkpoint.output_offset
    with open(output_path, "rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(_CHUNK, remaining))
            if not chunk:
                raise ResumeError("output ended before the checkpoint offset")
            digest.update(chunk)
            remaining -= len(chunk)
    if digest.hexdigest() != checkpoint.prefix_digest:
        raise ResumeError("committed output prefix digest does not match the checkpoint")


def commit_output(handle) -> None:
    """Flush and fsync the output so a following checkpoint names durable bytes."""
    handle.flush()
    os.fsync(handle.fileno())


def write_checkpoint(path, checkpoint: Checkpoint) -> None:
    """Atomically write ``checkpoint`` to ``path`` (temp file + os.replace).

    The caller commits the output first. Raises OSError on write failure; the
    live checkpoint is left intact because the temp file is replaced only once
    fully written and fsynced."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(checkpoint.to_dict(), handle, ensure_ascii=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name == "posix":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, path)
