"""
SQLite persistence adapter.

Outbound: snapshots the source machine after every event and writes the
snapshot to a `machine_states` table keyed by (machine_class, instance_id).

Inbound: provides an instance loader that rehydrates machines from the DB
via `Machine.restore(snapshot)` when an event targets an instance not
currently in memory.

This is the bidirectional persistence adapter described in
docs/06-io-adapters.md. It exercises both `snapshot()`/`restore()` on the
machine side and `set_instance_loader()` on the runner side.

The adapter contains no domain logic. It serializes JSON-compatible dicts
to a single TEXT column. It does not interpret payloads, guard transitions,
or make any decision a machine could make.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.adapters import ProductionRunner
    from sdd.events import Event
    from sdd.protocol import StateMachine


_SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_states (
    machine_class TEXT NOT NULL,
    instance_id   TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (machine_class, instance_id)
);
"""


class SqlitePersistenceAdapter:
    """Persistence adapter using SQLite as the backing store."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._runner: "ProductionRunner | None" = None
        self._conn: sqlite3.Connection | None = None
        # Map class name -> class object, populated at attach() from the runner
        self._classes_by_name: dict[str, type] = {}

    # ---------- adapter lifecycle ----------

    def attach(self, runner: "ProductionRunner") -> None:
        """Wire up subscriptions and the instance loader."""
        self._runner = runner
        # Snapshot every event from every machine
        runner.event_bus.subscribe_pattern("*", self._on_event)
        # Register as the instance loader for rehydration
        runner.set_instance_loader(self._load_instance)
        # Remember the registered machine classes so we can resolve by name
        for cls in runner._machine_classes:
            self._classes_by_name[cls.__name__] = cls

    def startup(self) -> None:
        """Open the DB connection and ensure the schema exists.

        `check_same_thread=False` lets the connection be used from the HTTP
        adapter's worker thread. Safe because bus delivery is synchronous
        and HTTPServer (not ThreadingHTTPServer) processes one request at a time.
        """
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def shutdown(self) -> None:
        """Close the DB connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------- outbound: snapshot on every event ----------

    def _on_event(self, event: "Event") -> None:
        if self._runner is None or self._conn is None:
            return
        cls = self._classes_by_name.get(event.source_machine)
        if cls is None:
            return
        instance = self._runner.get(cls, event.source_instance)
        if instance is None:
            return
        snapshot = instance.snapshot()
        self._conn.execute(
            "INSERT OR REPLACE INTO machine_states "
            "(machine_class, instance_id, snapshot_json, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (
                event.source_machine,
                event.source_instance,
                json.dumps(snapshot),
                event.timestamp,
            ),
        )
        self._conn.commit()

    # ---------- inbound: rehydrate from DB ----------

    def _load_instance(self, machine_class: type, instance_id: str) -> "StateMachine | None":
        if self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT snapshot_json FROM machine_states "
            "WHERE machine_class = ? AND instance_id = ?",
            (machine_class.__name__, instance_id),
        ).fetchone()
        if row is None:
            return None
        snapshot = json.loads(row[0])
        return machine_class.restore(snapshot)
