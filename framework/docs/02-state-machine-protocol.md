# State Machine Protocol

## Purpose

Every state machine in the system must conform to this protocol. The protocol exists so that Claude Code can uniformly operate any machine — inspect its state, enumerate available transitions, fire transitions, and observe results — without knowing the machine's domain-specific details.

## Base Class: `StateMachine`

All machines inherit from a single base class. The base class provides the protocol; subclasses define states, transitions, guards, and side effects.

```python
from sdd.protocol import StateMachine, State

class OrderLifecycle(StateMachine):
    """Tracks an order from creation through fulfillment."""

    # --- States ---
    created = State(initial=True)
    validated = State()
    fulfilled = State()
    completed = State(final=True)
    cancelled = State(final=True)

    # --- Transitions ---
    validate = created.to(validated) | created.to(cancelled)
    fulfill = validated.to(fulfilled)
    complete = fulfilled.to(completed)
    cancel = (created | validated | fulfilled).to(cancelled)

    # --- Guards ---
    def guard_validate_to_validated(self, **kwargs) -> bool:
        """Only valid if items and customer are present."""
        return bool(
            self.context.get("items") and self.context.get("customer_id")
        )

    # --- Side Effects ---
    def on_enter_validated(self):
        """Emit event when order passes validation."""
        self.emit("order.validated", {
            "order_id": self.context["order_id"],
            "items": self.context["items"],
        })

    def on_enter_cancelled(self):
        self.emit("order.cancelled", {
            "order_id": self.context["order_id"],
            "reason": self.context.get("cancellation_reason", "unspecified"),
        })
```

## Protocol Requirements

### States

Each state is declared as a class-level `State()` descriptor. States have two optional flags:

- `initial=True` — exactly one state per machine must be initial. The machine starts here.
- `final=True` — the machine considers itself complete in this state. No transitions fire from final states.

Every state must be reachable from the initial state. Unreachable states are a structural error caught during convergence.

### Transitions

Transitions are declared as class-level descriptors connecting source states to target states.

A transition with a single target is **deterministic**: `validate = created.to(validated)`.

A transition with multiple targets is **guarded**: `validate = created.to(validated) | created.to(cancelled)`. When a guarded transition fires, the machine evaluates guards in declaration order and takes the first branch whose guard returns `True`. If no guard passes, the transition fails.

A transition can have multiple source states: `cancel = (created | validated).to(cancelled)`. This means the same transition name can fire from either source state.

### Guards

Guards are methods named `guard_{transition}_to_{target}`. They receive the same keyword arguments passed to the transition call and return a boolean.

```python
def guard_validate_to_validated(self, **kwargs) -> bool:
    ...

def guard_validate_to_cancelled(self, **kwargs) -> bool:
    ...
```

Guards must be **pure functions of machine context and transition arguments**. They must not perform I/O, access external state, or mutate context. They answer one question: "Is this branch allowed right now?"

If no explicit guard is defined for a branch, it defaults to `True` (always allowed). For guarded transitions (multiple targets), the last branch typically has no guard and serves as the fallback.

### Context

Every machine instance carries a `context: dict` — a mutable dictionary holding the machine's working data. Context is initialized at machine creation and updated through transition arguments.

```python
order = OrderLifecycle(context={
    "order_id": "ord_123",
    "customer_id": "cust_456",
    "items": [{"sku": "A1", "qty": 2}],
})
```

When a transition fires, keyword arguments are merged into context:

```python
order.fire("cancel", cancellation_reason="customer_request")
# order.context["cancellation_reason"] is now "customer_request"
```

Context is the mechanism by which machines accumulate information over their lifecycle.

### Side Effects

Side effects are methods triggered by state changes:

- `on_exit_{state}()` — called when leaving a state
- `on_enter_{state}()` — called when entering a state
- `on_transition_{transition}()` — called when a transition fires, after exit and before enter

Side effects may read and write context. Their primary purpose is emitting events to the event bus via `self.emit(event_name, payload)`. They should **not** perform I/O directly.

### Event Emission

Machines communicate by emitting events: `self.emit("order.validated", payload)`. The base class connects to the event bus (provided at instantiation). Emitted events are delivered to all subscribers after the current transition completes.

Events are the **only** mechanism by which machines influence each other. No machine holds a reference to another machine. No machine calls another machine's methods.

### Event Schemas

A machine may declare payload schemas for the events it emits via a class-level `EVENT_SCHEMAS` dict. The runner registers these on the bus at `register()` time, and the bus validates payloads at emission. A schema-violating emit raises `SchemaValidationError`, which the runner catches and records as a `schema_validation_failed` `StepError`. The invalid event does **not** enter the log and is not delivered.

```python
class OrderLifecycle(StateMachine):
    EVENT_SCHEMAS = {
        "order.validated": {
            "required": ["order_id", "items"],
            "properties": {
                "order_id": {"type": "string"},
                "items": {"type": "array"},
            },
        },
        "order.cancelled": {"required": ["order_id", "reason"]},
    }
    ...
```

Supported schema keys: `required` (list of field names) and `properties` (per-field type checks). Type names: `string`, `number`, `integer`, `boolean`, `array`, `object`, `null`. Unknown fields in a payload are allowed. The emitter is authoritative — if two machines try to register conflicting schemas for the same event name, `EventBus.register_schema` raises `ValueError`.

This is the mechanism that catches event-shape bugs at the source rather than as downstream state-assertion failures. See `docs/03-event-system.md` for the bus-level details.

### Time-Based Transitions (@timeout)

A machine may declare that a transition fires automatically after the instance has spent some duration in a particular state. The runner tracks per-instance state-entry timestamps and fires the transition when `runner.advance_time(...)` advances the virtual clock past the deadline.

```python
from sdd.protocol import State, StateMachine
from sdd.timing import timeout

class PaymentFlow(StateMachine):
    pending = State(initial=True)
    authorized = State(final=True)
    expired = State(final=True)

    authorize = pending.to(authorized)
    expire = pending.to(expired)

    @timeout(expire, hours=24)
    def on_deadline(self):
        """Runs after the timeout-driven `expire` fires. Like on_transition_*."""
        self._context["expiry_reason"] = "authorization_timeout"
```

The decorator accepts `seconds`, `minutes`, `hours`, `days` (cumulative) and an optional `from_state` if the source state can't be inferred from the transition's first declared branch. Timeouts are cancelled automatically when the instance leaves the source state through any other transition. See `docs/04-simulation-runner.md` for the virtual clock semantics.

### Introspection API

The base class exposes these read-only properties for Claude Code's use:

| Property | Type | Description |
|---|---|---|
| `current_state` | `str` | Name of the current state |
| `is_final` | `bool` | Whether the current state is final |
| `available_transitions` | `list[str]` | Transitions that can fire from the current state |
| `all_states` | `list[str]` | All declared states |
| `all_transitions` | `list[TransitionInfo]` | All declared transitions with source/target info |
| `context` | `dict` | Current context (read-only copy for introspection) |
| `history` | `list[TransitionRecord]` | Ordered log of all transitions fired |

### Firing Transitions

```python
result: TransitionResult = machine.fire("validate", **kwargs)
```

`TransitionResult` contains:

```python
@dataclass
class TransitionResult:
    success: bool             # Whether the transition fired
    source: str               # State before the transition
    target: str               # State after (same as source if failed)
    transition: str           # Name of the transition attempted
    failure_reason: str | None  # Why it failed, if it did
    events_emitted: list[Event]  # Events produced during this transition
```

Transitions can fail for exactly three reasons:
1. The transition name doesn't exist → `"unknown_transition"`
2. The transition can't fire from the current state → `"invalid_source_state"`
3. No guard returned True for any branch → `"no_guard_passed"`

### Serialization

Machines must be serializable for persistence and replay:

```python
snapshot: dict = machine.snapshot()
# Returns: {"state": "validated", "context": {...}, "history": [...]}

machine = OrderLifecycle.restore(snapshot, event_bus=bus)
# Reconstructs machine in the snapshotted state
```

`snapshot()` returns a plain dict with no object references. `restore()` is a classmethod that reconstitutes a machine from a snapshot. These methods enable the persistence adapter and simulation replay.

## Constraints

These constraints are enforced structurally. Violating them is a protocol error caught before simulation begins.

1. Exactly one initial state per machine.
2. At least one final state per machine.
3. No imports outside the standard library and `sdd.protocol`.
4. Guards must not mutate context.
5. Side effects must not perform I/O (no network, filesystem, or database calls).
6. All inter-machine communication goes through `self.emit()`.
