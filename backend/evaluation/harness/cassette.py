"""A recording of every external answer a run received, keyed by the question.

What makes a run reproducible. The scholarly APIs change daily and the local
models sample, so two live runs of the same case differ for reasons that have
nothing to do with the code under test. With the answers recorded, a rerun sees
the same papers and the same drafts, and a difference between two runs is a
difference in *our* code — which is what a regression check needs.

One SQLite file, one table, namespaced by kind (``http``, ``llm``, ``emb``,
``judge``). Keys are SHA-256 of a canonical JSON of the request, with
credentials removed before hashing, so a recording made with an API key replays
without one.

What is never recorded: a 429, a 5xx, a transport error, or PubMed's 200-status
"API rate limit exceeded" body. Recording a transient failure would turn it
into a permanent one, and every replay would then measure an outage that
happened once.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def stable_key(*parts: Any) -> str:
    canonical = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class Cassette:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " ns TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,"
            " latency_ms REAL, created_at TEXT NOT NULL, PRIMARY KEY (ns, key))"
        )
        self._db.commit()

    def get(self, ns: str, key: str) -> tuple[Any, float | None] | None:
        row = self._db.execute(
            "SELECT value, latency_ms FROM entries WHERE ns = ? AND key = ?", (ns, key)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), row[1]

    def put(self, ns: str, key: str, value: Any, latency_ms: float | None = None) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO entries (ns, key, value, latency_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                ns,
                key,
                json.dumps(value, default=str),
                latency_ms,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        self._db.commit()

    def counts(self) -> dict[str, int]:
        return dict(self._db.execute("SELECT ns, COUNT(*) FROM entries GROUP BY ns").fetchall())

    def close(self) -> None:
        self._db.close()
