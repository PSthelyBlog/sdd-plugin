# I/O Adapter Specification

## Purpose

I/O adapters connect the production-core state machines to real infrastructure. They are written **after convergence**, are deliberately thin, and contain no domain logic. Their existence is the reason the state machine core can remain pure.

## Adapter Taxonomy

### Inbound Adapters

Inbound adapters translate external signals into machine transitions.

```
External Signal ──▶ Inbound Adapter ──▶ runner.fire(instance, Machine, transition, **args)
```

An inbound adapter does exactly three things:

1. **Deserialize** the external input into a Python dict.
2. **Resolve** which machine instance should receive the action (e.g., extract `order_id` from a request path).
3. **Fire** the appropriate transition on the runner with the extracted arguments.

```python
# Example: FastAPI inbound adapter
@app.post("/orders/{order_id}/validate")
async def validate_order(order_id: str, body: ValidateRequest):
    result = runner.fire(order_id, OrderLifecycle, "validate",
                         items=body.items, customer_id=body.customer_id)
    if result.success:
        return {"status": "ok", "state": result.target}
    else:
        raise HTTPException(400, detail=result.failure_reason)
```

**The adapter does not decide whether validation succeeds.** That's the machine's guard. The adapter just passes data through.

If an inbound adapter contains conditional logic about business rules, that logic has escaped the core and must be moved into a machine guard.

### Outbound Adapters

Outbound adapters subscribe to machine events and perform external side effects.

```
Machine event ──▶ Event Bus ──▶ Outbound Adapter ──▶ External System
```

Outbound adapters register as event subscribers, just like machines do, but instead of firing transitions, they perform I/O:

```python
# Example: Persistence outbound adapter
class PersistenceAdapter:
    def __init__(self, db, runner):
        runner.event_bus.subscribe_pattern("*", self.on_any_event)

    def on_any_event(self, event: Event):
        # Snapshot the source machine's state after every event
        machine = runner.get_by_event(event)
        snapshot = machine.snapshot()
        self.db.upsert(
            table="machine_states",
            key=(event.source_machine, event.source_instance),
            data=snapshot,
        )

# Example: Notification outbound adapter
class NotificationAdapter:
    def __init__(self, email_service, runner):
        runner.event_bus.subscribe("order.completed", self.on_order_completed)
        runner.event_bus.subscribe("payment.failed", self.on_payment_failed)

    def on_order_completed(self, event: Event):
        self.email_service.send(
            to=event.payload["customer_email"],
            template="order_complete",
            data=event.payload,
        )

    def on_payment_failed(self, event: Event):
        self.email_service.send(
            to=event.payload["customer_email"],
            template="payment_failed",
            data=event.payload,
        )
```

### Persistence Adapter (Bidirectional)

The persistence adapter is unique because it operates in both directions:

**Outbound (save):** After state changes, serialize and store machine snapshots.

**Inbound (load):** When a transition arrives for an instance that isn't in memory, rehydrate it from storage using `Machine.restore(snapshot)`.

```python
class PersistenceAdapter:
    def __init__(self, db, runner):
        runner.set_instance_loader(self.load_instance)
        runner.event_bus.subscribe_pattern("*", self.save_snapshot)

    def load_instance(self, machine_class, instance_id):
        """Called by runner when an instance is needed but not in memory."""
        row = self.db.get(
            table="machine_states",
            key=(machine_class.__name__, instance_id),
        )
        if row:
            return machine_class.restore(row["data"], event_bus=runner.event_bus)
        return None  # Instance doesn't exist yet

    def save_snapshot(self, event: Event):
        machine = runner.get_by_event(event)
        self.db.upsert(
            table="machine_states",
            key=(event.source_machine, event.source_instance),
            data=machine.snapshot(),
        )
```

## Adapter Design Rules

### Rule 1: No Domain Logic

An adapter must not contain `if` statements that evaluate business rules. All of the following belong in machine guards or side effects, not adapters:

- Whether an order can be cancelled at this stage
- Whether a payment amount is valid
- Whether inventory is sufficient
- Whether a customer is authorized

The adapter may contain infrastructure-level conditionals (retry logic, circuit breaking, format validation) but never domain-level ones.

### Rule 2: Adapters Are Replaceable

The state machine core must function identically regardless of which adapters are attached. Swap FastAPI for a CLI adapter, swap Postgres for SQLite, swap email for SMS — the core's behavior does not change.

This is testable: the simulation runner is proof that the core works with no adapters at all.

### Rule 3: Adapters Fail Gracefully

Adapter failures must not corrupt machine state. If a persistence write fails, the in-memory machine state is still correct. If a notification fails, the order's state is unaffected.

This means adapters should handle their own error recovery (retries, dead letter queues, circuit breakers) without propagating failures back into the state machine core.

### Rule 4: Adapters Are Independently Testable

Each adapter is tested against a mock or test instance of its external dependency (a test database, a mock email service). These tests verify the adapter's wiring, not the domain logic.

Adapter tests are small and mechanical:
- Does the HTTP adapter correctly extract `order_id` from the path?
- Does the persistence adapter correctly serialize and deserialize snapshots?
- Does the notification adapter send the right template for each event?

Domain correctness is already proven by simulation. Adapter tests only verify the plumbing.

## Adapter Protocol

Adapters satisfy the `Adapter` runtime-checkable Protocol defined in `sdd/adapters.py`:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Adapter(Protocol):
    def attach(self, runner: "ProductionRunner") -> None: ...
    # startup() and shutdown() are optional lifecycle hooks
```

Required: `attach(runner)` — wires up subscriptions, registers loaders, captures any references the adapter needs from the runner. Called once when the adapter is attached.

Optional: `startup()` / `shutdown()` — lifecycle hooks for opening DB connections, starting background threads, etc. Called by `ProductionRunner.startup()` and `ProductionRunner.shutdown()`. Shutdown order is the reverse of attach order.

Implementations can be any class that satisfies the protocol — no inheritance is required.

## Production Runner

In production, `ProductionRunner` extends `SimulationRunner` with adapter lifecycle management and instance rehydration:

```python
from sdd.adapters import ProductionRunner
from adapters.outbound.sqlite_persistence import SqlitePersistenceAdapter
from adapters.inbound.http_api import HttpInboundAdapter

runner = ProductionRunner()
runner.register(OrderLifecycle)
runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))

runner.attach(SqlitePersistenceAdapter("orders.db"))
runner.attach(HttpInboundAdapter(host="0.0.0.0", port=8080))

with runner:  # calls startup() on all adapters; shutdown() on exit
    serve_forever()
```

Or with explicit lifecycle:

```python
runner.startup()
try:
    serve_forever()
finally:
    runner.shutdown()
```

The production runner inherits `fire()`, event delivery, structural checks, scenario execution, coverage tracking, and convergence reporting unchanged from `SimulationRunner`. The only additions are adapter lifecycle and `set_instance_loader()` for the persistence-rehydration hook.

When an event targets an instance not currently in memory, `ProductionRunner.get()` consults the registered loader (typically the persistence adapter) before returning None. If the loader returns a snapshot-rehydrated instance, it is registered in the in-memory registry and used. This is how a process restart picks up where the previous one left off — see `adapters/outbound/sqlite_persistence.py` for the reference implementation.

## Reference Implementations

The repository ships two reference adapters demonstrating the contract end-to-end against `OrderLifecycle`:

| File | What it does |
|---|---|
| `adapters/outbound/sqlite_persistence.py` | `SqlitePersistenceAdapter` — bidirectional. Snapshots after every event; rehydrates via `Machine.restore()` on demand. SQLite-only, ~95 lines. |
| `adapters/inbound/http_api.py` | `HttpInboundAdapter` — stdlib `http.server` on a background thread. Routes `POST /machines/{class}/{id}/{transition}` to `runner.fire(...)`. Returns `StepResult` as JSON. No external deps, ~115 lines. |

`tests/test_integration_e2e.py` drives `OrderLifecycle` through both adapters end-to-end. The headline test (`test_restart_preserves_machine_state`) tears down the runner mid-scenario, brings up a fresh runner against the same SQLite file, and completes the scenario — confirming the snapshot/restore contract works across a real process boundary. `test_final_state_matches_simulation` directly compares simulation and production outcomes for the same scenario and asserts they are identical.

## Adapter Development Workflow

1. **Core converges** — all structural, scenario, and invariant checks pass (`runner.converge().is_converged`).
2. **Claude Code inventories I/O needs** — what external signals come in? What side effects go out? What must be persisted?
3. **Subagents write adapters** — one adapter per external system, following the rules above.
4. **Adapters are tested independently** — against mocks or in-memory fakes, not the full system.
5. **Integration test** — `ProductionRunner` with all adapters attached processes the same scenarios from convergence testing, now with real (or containerized) infrastructure.

The integration test should produce the same machine states and events as the simulation. If it doesn't, the bug is in an adapter, not the core.
