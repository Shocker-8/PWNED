"""Focused checks for the bounded SQLite exact-dedupe backend."""
import os

import pytest

from candidatelab.dedupe import DedupeError, ExactDedupe, InMemoryDedupe, make_dedupe


def test_single_key_detects_duplicates_and_preserves_novelty():
    with ExactDedupe(1) as store:
        assert not store.contains((b"alpha",))
        store.add((b"alpha",))
        assert store.contains((b"alpha",))
        assert not store.contains((b"beta",))


def test_pairs_use_separate_key_fields():
    with ExactDedupe(2) as store:
        store.add((b"user", b"pass"))
        assert store.contains((b"user", b"pass"))
        # Same username, different password is a distinct key.
        assert not store.contains((b"user", b"other"))
        # Swapping the fields is also distinct.
        assert not store.contains((b"pass", b"user"))


def test_invalid_key_shape_is_rejected():
    with ExactDedupe(1) as store:
        with pytest.raises(ValueError):
            store.add((b"a", b"b"))
        with pytest.raises(ValueError):
            store.add(("not-bytes",))


def test_disk_budget_exceeded_raises_dedupe_error():
    # A tiny disk limit and batch force a commit/size-check that trips quickly.
    store = ExactDedupe(1, disk_limit_bytes=1, batch=1)
    try:
        with pytest.raises(DedupeError):
            for index in range(1000):
                store.add((f"key-{index}".encode(),))
    finally:
        store.close()


def test_close_removes_owned_temporary_directory():
    store = ExactDedupe(1)
    directory = store._dir
    assert os.path.isdir(directory)
    store.close()
    assert not os.path.exists(directory)
    # Idempotent close.
    store.close()


def test_temporary_files_use_restrictive_permissions_on_posix():
    if os.name != "posix":
        pytest.skip("POSIX permission check")
    with ExactDedupe(1) as store:
        assert (os.stat(store._dir).st_mode & 0o077) == 0
        assert (os.stat(store._path).st_mode & 0o077) == 0


def test_factory_returns_requested_backend():
    with make_dedupe("memory", 1) as store:
        assert isinstance(store, InMemoryDedupe)
    with make_dedupe("sqlite", 2) as store:
        assert isinstance(store, ExactDedupe)
    with pytest.raises(ValueError):
        make_dedupe("bloom", 1)


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
def test_duplicates_detected_across_batch_boundaries(backend):
    # batch=2 forces commits mid-stream for the SQLite backend; novelty must
    # still be exact across the boundary. The memory backend ignores batch.
    kwargs = {"batch": 2} if backend == "sqlite" else {}
    with make_dedupe(backend, 1, **kwargs) as store:
        for index in range(5):
            assert not store.contains((f"k{index}".encode(),))
            store.add((f"k{index}".encode(),))
        # Keys from before and after the first commit are all still known.
        for index in range(5):
            assert store.contains((f"k{index}".encode(),))


def test_in_memory_key_ceiling_raises():
    store = InMemoryDedupe(1, max_keys=2)
    store.add((b"a",))
    store.add((b"b",))
    store.add((b"a",))  # duplicate does not grow the set
    with pytest.raises(DedupeError):
        store.add((b"c",))
