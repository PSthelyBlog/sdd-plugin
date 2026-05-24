# Event System

## Purpose

The event system is the sole communication channel between state machines. It serves two roles: delivering events between machines during operation, and recording a complete, ordered log of all events for Claude Code's analysis.

## Event Structure

```python
@dataclass(frozen=True)
class Event:
    name: str                  # Dot-namespaced identifier, e.g. "order.validated"
    payload: dict              # Arbitrary data, serializable to JSON
    source_machine: str        # Class name of the emitting machine
    source_instance: str       # Instance identifier (e.g. order_id)
    timestamp: float           # Monotonic clock for ordering
    correlation_id: str        # Groups events from the same originating action
```

Events are immutable. Once created, they cannot be modified. This is essential for replay and debugging.

## Event Naming Convention

Events follow a `{domain}.{action}` pattern:

```
order.validated
order.cancelled
payment.authorized
payment.failed
inventory.reserved
inventory.released
```

The domain prefix matches the machine that emits the event. The action suffix describes what happened, in past tense. Events are facts about what already occurred, not commands about what should happen.

## Event Bus

### Interface

```python
class EventBus:
    def emit(self, event: Event) -> None:
        """Record the event and deliver to subscribers."""

    def subscribe(self, event_name: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for events matching the given name."""

    def subscribe_pattern(self, pattern: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for events matching a glob pattern."""
        # e.g., "order.*" matches all order events

    @property
    def log(self) -> list[Event]:
        """Complete ordered log of all events emitted."""
```

### Delivery Semantics

Events are delivered **synchronously and in order** within the simulation runner's step cycle. When a machine emits an event during a transition:

1. The event is appended to the log.
2. The event is placed in a delivery queue.
3. After the emitting transition completes (all side effects finish), the queue is drained.
4. Each subscriber receives the event. If a subscriber triggers a transition on another machine, any events that machine emits are added to the queue.
5. The queue drains until empty. One external stimulus can cascade through multiple machines.

This is depth-first, synchronous delivery. It mirrors what would happen in a single-process production deployment and is the simplest model for Claude Code to reason about.

### Cascade Limits

To prevent infinite loops, the bus enforces a maximum cascade depth (default: 20). If an event triggers a chain of reactions that exceeds this depth, the bus halts and reports the cycle to Claude Code. This is almost always a modeling error — a circular dependency between machines.

## Subscriptions

Machines declare their subscriptions through a class-level method:

```python
class InventoryState(StateMachine):
    available = State(initial=True)
    reserved = State()
    allocated = State()
    released = State(final=True)

    reserve = available.to(reserved)
    allocate = reserved.to(allocated)
    release = (reserved | allocated).to(released)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        """Map event names to transition names."""
        return {
            "order.validated": "reserve",
            "order.cancelled": "release",
            "order.fulfilled": "allocate",
        }
```

The `subscriptions()` classmethod returns a mapping from event name to transition name. When the bus delivers a matching event, the simulation runner locates the appropriate machine instance and fires the corresponding transition, passing the event payload as keyword arguments.

This keeps the subscription declarations co-located with the machine, visible to Claude Code during structural analysis, and free of any infrastructure coupling.

## Instance Resolution

When an event arrives for a machine type, the system must determine **which instance** should receive it. This is handled by a resolver function registered with the simulation runner:

```python
runner.register_resolver(
    InventoryState,
    lambda event: event.payload.get("order_id")
)
```

The resolver extracts an instance key from the event payload. The runner looks up or creates a machine instance for that key. This is the same mechanism the persistence adapter uses in production.

## Event Log

The event log is the primary artifact Claude Code uses for analysis. It is an append-only, ordered list of all events emitted during a simulation run.

### Log Queries

The log supports queries used by Claude Code during convergence checking:

```python
# All events from a specific machine type
log.filter(source_machine="OrderLifecycle")

# All events with a specific name
log.filter(name="payment.failed")

# All events in a correlation group
log.filter(correlation_id="corr_abc123")

# Events within a time range
log.filter(after=t1, before=t2)

# Chained filters
log.filter(source_machine="PaymentFlow").filter(name="payment.failed")
```

### Log as Production Asset

In production, the event log maps directly to an event store or structured logging system. The same `Event` dataclass serializes to JSON for durable storage. Because the log format is defined during simulation, the production event store schema is a known quantity before any adapter code is written.

## Dead Letter Detection

During structural analysis, Claude Code checks that every event emitted by any machine has at least one subscriber. An event with no subscribers is a **dead letter** — it indicates either a missing machine, a missing subscription, or an event that should not be emitted.

Dead letters are not errors in all cases (some events may be consumed only by outbound adapters in production), but they are flagged for review during convergence.

## Event Schema Validation

Event payloads follow schemas defined alongside the machines:

```python
EVENT_SCHEMAS = {
    "order.validated": {
        "required": ["order_id", "items"],
        "properties": {
            "order_id": {"type": "string"},
            "items": {"type": "array"},
        }
    },
    "payment.authorized": {
        "required": ["order_id", "amount", "transaction_id"],
        "properties": {
            "order_id": {"type": "string"},
            "amount": {"type": "number"},
            "transaction_id": {"type": "string"},
        }
    },
}
```

The bus validates payloads against schemas on emission. Schema violations are immediate errors, not deferred to consumption. This catches integration mismatches during simulation, not production.
