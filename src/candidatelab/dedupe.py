"""Exact, order-preserving deduplication backed by a bounded SQLite table.

The engine stays the behavioral reference: candidates are emitted in first-seen
order, and only the keys — not the candidates — are stored. Keys are exact BLOBs
(credential pairs keep username and password in separate columns), so no short
hash or Bloom filter ever decides what to drop. Memory is bounded by the SQLite
page cache; temporary disk use is bounded by an explicit ceiling. The owned
temporary directory is removed on close; user inputs are never touched.

Note: for very large runs the SQLite/disk dedupe can become the throughput
bottleneck. That is the deliberate trade for exactness without unbounded memory.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile

# Memory ceiling for the page cache (negative = KiB); keeps RSS bounded.
_CACHE_KIB = 2000
# Commit after this many novel keys to bound the active transaction.
_BATCH = 1000
# Default temporary-disk ceiling for the key table (1 GiB).
_DEFAULT_DISK_LIMIT = 1 << 30


class DedupeError(ValueError):
    """Raised for database, disk-capacity or cache failures. Subclasses
    ValueError so the engine reports it as an explicitly partial export."""


def _normalize(key, fields):
    if not isinstance(key, (tuple, list)) or len(key) != fields:
        raise ValueError(f"key must be a tuple of {fields} byte strings")
    if any(not isinstance(part, (bytes, bytearray)) for part in key):
        raise ValueError("key parts must be bytes")
    return tuple(bytes(part) for part in key)


class ExactDedupe:
    """Bounded exact-key store. ``fields`` is 1 (single key) or 2 (pairs).

    ``contains(key)`` reports whether an exact key was already added; ``add(key)``
    records a novel key. ``key`` is a tuple of ``fields`` bytes objects. Both raise
    DedupeError on backend or disk-budget failure. ``close()`` (also the context
    manager exit) commits, closes and removes the owned temporary directory.
    """

    def __init__(self, fields: int = 1, *, parent_dir=None,
                 disk_limit_bytes: int = _DEFAULT_DISK_LIMIT, batch: int = _BATCH):
        if fields not in (1, 2):
            raise ValueError("fields must be 1 (single key) or 2 (pairs)")
        self._fields = fields
        self._disk_limit = disk_limit_bytes
        self._batch = max(1, batch)
        self._pending = 0
        self._closed = False
        # Owned temp dir with restrictive permissions on POSIX; 0o700 is a no-op
        # on platforms that ignore the mode.
        self._dir = tempfile.mkdtemp(prefix="candidatelab-dedupe-", dir=parent_dir)
        try:
            os.chmod(self._dir, 0o700)
        except OSError:
            pass
        self._path = os.path.join(self._dir, "keys.sqlite3")
        try:
            self._conn = sqlite3.connect(self._path, isolation_level="DEFERRED")
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
            self._conn.execute(f"PRAGMA cache_size=-{_CACHE_KIB}")
            self._conn.execute("PRAGMA journal_mode=OFF")
            self._conn.execute("PRAGMA synchronous=OFF")
            self._conn.execute("PRAGMA temp_store=MEMORY")
            columns = "u BLOB NOT NULL" if fields == 1 else "u BLOB NOT NULL, p BLOB NOT NULL"
            key = "u" if fields == 1 else "u, p"
            self._conn.execute(f"CREATE TABLE seen ({columns}, PRIMARY KEY ({key}))")
        except sqlite3.Error as exc:
            self._cleanup()
            raise DedupeError(f"could not initialize dedupe database: {exc}") from exc

    def _normalize(self, key):
        return _normalize(key, self._fields)

    def contains(self, key) -> bool:
        parts = self._normalize(key)
        where = "u=?" if self._fields == 1 else "u=? AND p=?"
        try:
            row = self._conn.execute(f"SELECT 1 FROM seen WHERE {where} LIMIT 1", parts).fetchone()
        except sqlite3.Error as exc:
            raise DedupeError(f"dedupe lookup failed: {exc}") from exc
        return row is not None

    def add(self, key) -> None:
        parts = self._normalize(key)
        placeholders = "?" if self._fields == 1 else "?, ?"
        try:
            self._conn.execute(f"INSERT OR IGNORE INTO seen VALUES ({placeholders})", parts)
        except sqlite3.Error as exc:
            raise DedupeError(f"dedupe insert failed: {exc}") from exc
        self._pending += 1
        if self._pending >= self._batch:
            self._flush()

    def _flush(self) -> None:
        try:
            self._conn.commit()
        except sqlite3.Error as exc:
            raise DedupeError(f"dedupe commit failed: {exc}") from exc
        self._pending = 0
        if self._disk_limit is not None:
            try:
                size = os.path.getsize(self._path)
            except OSError:
                size = 0
            if size > self._disk_limit:
                raise DedupeError(
                    f"dedupe temporary disk budget exceeded ({size} > {self._disk_limit} bytes)")

    def _cleanup(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except (sqlite3.Error, AttributeError):
            pass
        self._cleanup()

    def __enter__(self) -> "ExactDedupe":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class InMemoryDedupe:
    """Fast exact-key store kept in a Python set. Bounded by ``max_keys`` so
    memory stays bounded; raises DedupeError when the ceiling is reached. Shares
    the contains(key)/add(key)/close() interface with ExactDedupe. Useful for
    small runs and as the reference the SQLite backend must match byte for byte.
    """

    def __init__(self, fields: int = 1, *, max_keys: int = 5_000_000):
        if fields not in (1, 2):
            raise ValueError("fields must be 1 (single key) or 2 (pairs)")
        self._fields = fields
        self._max_keys = max_keys
        self._seen: set = set()

    def contains(self, key) -> bool:
        return _normalize(key, self._fields) in self._seen

    def add(self, key) -> None:
        parts = _normalize(key, self._fields)
        if self._max_keys is not None and len(self._seen) >= self._max_keys and parts not in self._seen:
            raise DedupeError(f"in-memory dedupe key ceiling reached ({self._max_keys})")
        self._seen.add(parts)

    def close(self) -> None:
        self._seen = set()

    def __enter__(self) -> "InMemoryDedupe":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def make_dedupe(backend: str, fields: int = 1, **kwargs):
    """Return a dedupe store for ``backend`` ("sqlite" or "memory").

    Both backends share the contains/add/close/context-manager interface and
    produce identical dedup decisions. Raises ValueError for an unknown backend.
    """
    if backend == "sqlite":
        return ExactDedupe(fields, **kwargs)
    if backend == "memory":
        return InMemoryDedupe(fields, **kwargs)
    raise ValueError("dedupe backend must be 'sqlite' or 'memory'")
