"""Bounded export with deterministic profiles, mutation priorities and provenance."""
from collections import deque
import os
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from .checkpoint import (Checkpoint, ResumeError, commit_output,
                         config_fingerprint, fingerprint_sources,
                         load_checkpoint, verify_inputs, verify_output_prefix,
                         write_checkpoint)
from .config import Config
from .dedupe import make_dedupe
from .mutations import Mutation, mutations
from .combinations import combinations
from .profiles import username_collisions


@dataclass
class Result:
    accepted: int = 0
    attempted: int = 0
    duplicates: int = 0
    filtered: int = 0
    quota_skipped: int = 0
    input_records: int = 0
    output_bytes: int = 0
    elapsed_seconds: float = 0.0
    stop_reason: str = "input_exhausted"
    profile_counts: dict[str, int] = field(default_factory=dict)
    family_attempts: dict[str, int] = field(default_factory=dict)
    username_collision_count: int = 0
    sha256: str = ""


class _BudgetStop(Exception):
    pass


class _Control:
    def __init__(self, config, result, clock, cancelled, progress=None):
        self.config, self.result = config, result
        self.clock, self.cancelled = clock, cancelled
        self.progress = progress
        self.started = clock()
        self._last_emit = self.started
        self._last_count = 0

    def check(self):
        if self.cancelled is not None and self.cancelled():
            raise _BudgetStop("cancelled")
        if self.config.max_seconds is not None and self.clock() - self.started >= self.config.max_seconds:
            raise _BudgetStop("time_budget")
        # Coalesced progress: at most every 256 attempts or 0.1s, never per candidate.
        if self.progress is not None:
            now = self.clock()
            if self.result.attempted - self._last_count >= 256 or now - self._last_emit >= 0.1:
                self._last_count, self._last_emit = self.result.attempted, now
                self.progress(self.result)


def _file_words(path, control):
    position = 0
    with ExitStack() as stack:
        persistent = stack.enter_context(Path(path).open("rb")) if control.config.source_scheduling == "ordered" else None
        while True:
            control.check()
            if control.result.input_records >= control.config.max_input_records:
                raise _BudgetStop("input_record_budget")
            # Ordered scans stay buffered. Interleaved scans reopen at saved byte offsets
            # so the number of active descriptors stays bounded independently of source count.
            with (nullcontext(persistent) if persistent else Path(path).open("rb")) as source:
                if not persistent:
                    source.seek(position)
                raw = source.readline(4099)
                position = source.tell()
            if not raw:
                return
            control.result.input_records += 1
            control.check()
            record = raw[:-1] if raw.endswith(b"\n") else raw
            if raw.endswith(b"\n") and record.endswith(b"\r"):
                record = record[:-1]
            if len(record) > 4096:
                raise ValueError("input record exceeds 4096 bytes")
            yield record.decode("utf-8", errors="strict")


def _words(seeds, paths, config, control):
    channels = [("seed", 0, iter(seeds)), ("combination", 0, iter(combinations(seeds, config.combinations)))]
    channels += [("wordlist", index, iter(_file_words(path, control))) for index, path in enumerate(paths)]
    queue = deque((kind, index, 0, source) for kind, index, source in channels)
    try:
        while queue:
            kind, index, rank, source = queue.popleft()
            control.check()
            try:
                word = next(source)
            except StopIteration:
                continue
            channel = (kind, index, rank + 1, source)
            if config.source_scheduling == "round_robin":
                queue.append(channel)
            else:
                queue.appendleft(channel)
            if word:
                yield word, {"source_kind": kind, "source_index": index, "source_rank": rank}
    finally:
        for _, _, source in channels:
            close = getattr(source, "close", None)
            if close:
                close()


def _candidates(words, names, config):
    if config.mode == "usernames":
        for rank, name in enumerate(names):
            yield name, None, {"source_kind": "profile", "source_index": 0, "source_rank": rank, "family": "username", "edit_cost": 0}
        return
    for seed, source in words:
        for mutation in mutations(seed, config.transforms, config.priorities):
            metadata = {**source, "family": mutation.family, "edit_cost": mutation.edit_cost}
            if config.mode == "pairs":
                for name in names:
                    yield name, mutation.value, metadata
            else:
                yield None, mutation.value, metadata


def _valid_password(word, config):
    return (bool(word) and (config.min_length is None or len(word) >= config.min_length)
            and (config.max_length is None or len(word) <= config.max_length)
            and (config.preset != "wpa2" or (8 <= len(word) <= 63
                 and all(32 <= ord(char) <= 126 for char in word))))


def _serialize(name, word, profile_id, metadata, config):
    if config.output_format == "text":
        return ((name if config.mode == "usernames" else word) + "\n").encode("utf-8")
    if config.mode == "passwords":
        record = {"password_candidate": word}
    elif config.mode == "usernames":
        record = {"username": name, "profile_id": profile_id}
    else:
        record = {"username": name, "password_candidate": word, "profile_id": profile_id}
    if config.provenance:
        record["provenance"] = {"profile_id": profile_id, **metadata}
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def run(config: Config, *, clock=None, cancelled=None, progress=None, dedupe_backend="sqlite",
        checkpoint_path=None, checkpoint_every=1000, resume_from=None) -> Result:
    # Resume via deterministic replay: validate the saved inputs and committed
    # output prefix up front, then regenerate from scratch, verifying the prefix
    # bytes rather than rewriting them, before appending new records. Startup cost
    # is O(previous work); this is not an efficient large-run checkpoint engine.
    resume_checkpoint = None
    if resume_from is not None:
        resume_checkpoint = load_checkpoint(resume_from)
        verify_inputs(config, resume_checkpoint)
        verify_output_prefix(config.output, resume_checkpoint)
        if checkpoint_path is None:
            checkpoint_path = resume_from
    result = Result(username_collision_count=len(username_collisions(config.profiles)))
    digest, jobs = hashlib.sha256(), deque()
    profiles = config.profiles or (None,)
    paths_by_profile = [tuple(dict.fromkeys(config.source_paths + (profile.inputs if profile else ()))) for profile in profiles]
    # Validate sources before creating output; the cooperative generation timer starts after preflight.
    unique_paths = list(dict.fromkeys(path for paths in paths_by_profile for path in paths))
    for path in unique_paths:
        with Path(path).open("rb"):
            pass
    # Resolve all mutable inputs before the run so checkpoints pin exact source state.
    checkpoint_meta = (config_fingerprint(config), fingerprint_sources(unique_paths)) if checkpoint_path else None
    control = _Control(config, result, clock or time.monotonic, cancelled, progress)

    def _write_cp(dest):
        commit_output(dest)
        config_fp, sources = checkpoint_meta
        write_checkpoint(checkpoint_path, Checkpoint(
            config_fingerprint=config_fp, sources=sources,
            emitted_count=result.accepted, output_offset=result.output_bytes,
            prefix_digest=digest.hexdigest(), attempted=result.attempted,
            duplicates=result.duplicates, filtered=result.filtered,
            quota_skipped=result.quota_skipped, input_records=result.input_records))

    with ExitStack() as stack:
        seen = stack.enter_context(make_dedupe(dedupe_backend, 2 if config.mode == "pairs" else 1))
        for profile, paths in zip(profiles, paths_by_profile):
            profile_id = profile.id if profile else "default"
            cap = profile.max_candidates if profile else config.max_candidates
            seeds = config.seeds + (profile.seeds if profile else ())
            names = profile.resolved_usernames if profile else ()
            words = _words(seeds, paths, config, control)
            iterator = _candidates(words, names, config)
            stack.callback(words.close)
            stack.callback(iterator.close)
            result.profile_counts[profile_id] = 0
            jobs.append((profile_id, cap, iterator))
        replay_remaining = resume_checkpoint.emitted_count if resume_checkpoint else 0
        prefix_reader = None
        with Path(config.output).open("r+b" if resume_checkpoint else "xb") as dest:
            if resume_checkpoint:
                # Drop any uncommitted trailing bytes, then append after the prefix.
                dest.truncate(resume_checkpoint.output_offset)
                dest.seek(resume_checkpoint.output_offset)
                prefix_reader = stack.enter_context(Path(config.output).open("rb"))
            elif os.name == "posix":
                # Profile-derived candidates can be sensitive; restrict on POSIX.
                try:
                    os.chmod(config.output, 0o600)
                except OSError:
                    pass
            try:
                while jobs:
                    control.check()
                    if result.accepted >= config.max_candidates:
                        result.stop_reason = "candidate_budget"
                        break
                    if result.attempted >= config.max_attempts:
                        result.stop_reason = "attempt_budget"
                        break
                    profile_id, cap, iterator = jobs.popleft()
                    if result.profile_counts[profile_id] >= cap:
                        result.stop_reason = "profile_budget"
                        continue
                    try:
                        name, word, metadata = next(iterator)
                    except StopIteration:
                        continue
                    jobs.append((profile_id, cap, iterator))
                    result.attempted += 1
                    family = metadata["family"]
                    quota = config.family_quotas.get(family)
                    used = result.family_attempts.get(family, 0)
                    if quota is not None and used >= quota:
                        result.quota_skipped += 1
                        continue
                    result.family_attempts[family] = used + 1
                    if config.mode != "usernames" and not _valid_password(word, config):
                        result.filtered += 1
                        continue
                    key = ((name.encode("utf-8"), word.encode("utf-8")) if config.mode == "pairs"
                           else ((name if config.mode == "usernames" else word).encode("utf-8"),))
                    if seen.contains(key):
                        result.duplicates += 1
                        continue
                    record = _serialize(name, word, profile_id, metadata, config)
                    if result.output_bytes + len(record) > config.max_output_bytes:
                        result.stop_reason = "byte_budget"
                        break
                    seen.add(key)
                    if replay_remaining > 0:
                        # Verify the regenerated record against the committed prefix
                        # instead of rewriting it; deterministic replay must match.
                        expected = prefix_reader.read(len(record))
                        if expected != record:
                            raise ResumeError("replayed record diverged from the committed output prefix")
                        replay_remaining -= 1
                    else:
                        dest.write(record)
                    digest.update(record)
                    result.accepted += 1
                    result.profile_counts[profile_id] += 1
                    result.output_bytes += len(record)
                    if checkpoint_meta is not None and replay_remaining == 0 and result.accepted % checkpoint_every == 0:
                        _write_cp(dest)
            except _BudgetStop as exc:
                result.stop_reason = str(exc)
            except BrokenPipeError:
                # Downstream reader closed the sink; stop cleanly, no traceback.
                result.stop_reason = "output_closed"
            except (ValueError, OSError) as exc:
                raise ValueError(f"generation failed; partial output at {config.output}: {exc}") from exc
            if replay_remaining > 0:
                raise ResumeError("generation did not reproduce the full committed prefix on resume")
            if checkpoint_meta is not None:
                # Final durable checkpoint; a dead sink cannot be synced, so skip then.
                try:
                    _write_cp(dest)
                except OSError:
                    pass
    result.sha256 = digest.hexdigest()
    result.elapsed_seconds = max(0.0, control.clock() - control.started)
    return result


def generate(input_path, output_path, max_candidates: int) -> int:
    """Compatibility entry: return accepted count; zero creates empty output."""
    return run(Config(input=str(input_path), output=str(output_path), max_candidates=max_candidates)).accepted
