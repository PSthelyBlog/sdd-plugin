"""Event system for inter-machine communication."""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Event:
    """Immutable event representing a fact about what happened."""

    name: str
    payload: dict
    source_machine: str
    source_instance: str
    timestamp: float = field(default_factory=time.monotonic)
    correlation_id: str = ""

    def __post_init__(self) -> None:
        # Freeze the payload dict by converting to a shallow copy
        # Note: frozen=True prevents reassignment, but dict contents are mutable
        # We make a copy to prevent external mutation
        object.__setattr__(self, "payload", dict(self.payload))


class SchemaValidationError(Exception):
    """Raised when an emitted event's payload does not match its declared schema."""

    def __init__(
        self,
        event_name: str,
        source_machine: str,
        source_instance: str,
        problems: list[str],
    ) -> None:
        self.event_name = event_name
        self.source_machine = source_machine
        self.source_instance = source_instance
        self.problems = problems
        problem_text = "; ".join(problems)
        super().__init__(
            f"Schema validation failed for '{event_name}' emitted by "
            f"{source_machine}('{source_instance}'): {problem_text}"
        )


# Minimal type-check helpers for schema validation.
_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


def validate_payload(
    payload: dict,
    schema: dict,
) -> list[str]:
    """
    Validate a payload dict against a minimal schema.

    Schema shape:
        {"required": [field_name, ...], "properties": {field: {"type": "string"}}}

    Both keys are optional. Unknown fields are allowed.

    Returns:
        List of human-readable problem descriptions. Empty list means valid.
    """
    problems: list[str] = []
    for required in schema.get("required", []):
        if required not in payload:
            problems.append(f"missing required field '{required}'")

    for field_name, field_schema in schema.get("properties", {}).items():
        if field_name not in payload:
            continue
        expected_type = field_schema.get("type")
        if expected_type is None:
            continue
        python_type = _TYPE_MAP.get(expected_type)
        if python_type is None:
            continue
        value = payload[field_name]
        if not isinstance(value, python_type):
            problems.append(
                f"field '{field_name}' expected type '{expected_type}', "
                f"got {type(value).__name__}"
            )
    return problems


class EventLog:
    """Query interface for filtering events."""

    def __init__(self, events: list[Event]) -> None:
        self._events = list(events)

    def __iter__(self):
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int) -> Event:
        return self._events[index]

    def filter(
        self,
        name: str | None = None,
        source_machine: str | None = None,
        source_instance: str | None = None,
        correlation_id: str | None = None,
    ) -> EventLog:
        """Filter events by any combination of fields."""
        result = self._events

        if name is not None:
            result = [e for e in result if e.name == name]
        if source_machine is not None:
            result = [e for e in result if e.source_machine == source_machine]
        if source_instance is not None:
            result = [e for e in result if e.source_instance == source_instance]
        if correlation_id is not None:
            result = [e for e in result if e.correlation_id == correlation_id]

        return EventLog(result)

    def after(self, timestamp: float) -> EventLog:
        """Filter events after the given timestamp."""
        return EventLog([e for e in self._events if e.timestamp > timestamp])

    def before(self, timestamp: float) -> EventLog:
        """Filter events before the given timestamp."""
        return EventLog([e for e in self._events if e.timestamp < timestamp])

    def unique_instances(self, machine_name: str) -> set[str]:
        """Get unique instance identifiers for a machine type."""
        return {
            e.source_instance
            for e in self._events
            if e.source_machine == machine_name
        }

    def to_list(self) -> list[Event]:
        """Return a copy of the events list."""
        return list(self._events)


class EventBus:
    """Central hub for event emission and subscription."""

    def __init__(
        self,
        max_cascade_depth: int = 20,
        strict_schemas: bool = True,
    ) -> None:
        self._log: list[Event] = []
        self._subscriptions: dict[str, list[Callable[[Event], None]]] = {}
        self._pattern_subscriptions: list[tuple[str, Callable[[Event], None]]] = []
        self._max_cascade_depth = max_cascade_depth
        self._cascade_depth = 0
        self._delivery_queue: list[Event] = []
        self._delivering = False
        # Event-name -> schema dict. Registered by the SimulationRunner when
        # registering a machine whose class declares EVENT_SCHEMAS.
        self._schemas: dict[str, dict] = {}
        self._strict_schemas = strict_schemas

    @property
    def log(self) -> EventLog:
        """Read-only access to the event log."""
        return EventLog(list(self._log))

    @property
    def schemas(self) -> dict[str, dict]:
        """Return a copy of the registered schemas (event_name -> schema)."""
        return dict(self._schemas)

    def register_schema(self, event_name: str, schema: dict) -> None:
        """
        Register a payload schema for an event name.

        If two machines register schemas for the same event name they must
        be identical; otherwise this raises ValueError.
        """
        existing = self._schemas.get(event_name)
        if existing is not None and existing != schema:
            raise ValueError(
                f"Conflicting schemas registered for event '{event_name}'. "
                f"Existing: {existing}; new: {schema}."
            )
        self._schemas[event_name] = schema

    def emit(self, event: Event) -> None:
        """Record the event and deliver to subscribers."""
        # Schema validation runs first so a bad payload never enters the log.
        schema = self._schemas.get(event.name)
        if schema is not None:
            problems = validate_payload(event.payload, schema)
            if problems and self._strict_schemas:
                raise SchemaValidationError(
                    event_name=event.name,
                    source_machine=event.source_machine,
                    source_instance=event.source_instance,
                    problems=problems,
                )

        self._log.append(event)
        self._delivery_queue.append(event)

        if not self._delivering:
            self._deliver_queued_events()

    def _deliver_queued_events(self) -> None:
        """Drain the delivery queue, delivering events to subscribers."""
        self._delivering = True
        try:
            while self._delivery_queue:
                self._cascade_depth += 1
                if self._cascade_depth > self._max_cascade_depth:
                    raise RuntimeError(
                        f"Event cascade exceeded maximum depth of {self._max_cascade_depth}. "
                        "This likely indicates a circular dependency between machines."
                    )

                event = self._delivery_queue.pop(0)
                self._deliver_event(event)

            self._cascade_depth = 0
        finally:
            self._delivering = False

    def _deliver_event(self, event: Event) -> None:
        """Deliver a single event to all matching subscribers."""
        # Exact match subscriptions
        handlers = self._subscriptions.get(event.name, [])
        for handler in handlers:
            handler(event)

        # Pattern subscriptions
        for pattern, handler in self._pattern_subscriptions:
            if fnmatch.fnmatch(event.name, pattern):
                handler(event)

    def subscribe(
        self, event_name: str, handler: Callable[[Event], None]
    ) -> None:
        """Register a handler for events matching the given name."""
        if event_name not in self._subscriptions:
            self._subscriptions[event_name] = []
        self._subscriptions[event_name].append(handler)

    def subscribe_pattern(
        self, pattern: str, handler: Callable[[Event], None]
    ) -> None:
        """Register a handler for events matching a glob pattern."""
        self._pattern_subscriptions.append((pattern, handler))

    def clear(self) -> None:
        """Clear all events and subscriptions. Used for testing."""
        self._log.clear()
        self._subscriptions.clear()
        self._pattern_subscriptions.clear()
        self._delivery_queue.clear()
        self._cascade_depth = 0
        self._delivering = False
