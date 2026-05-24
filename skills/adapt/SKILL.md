---
name: sdd-adapt
description: Scaffold inbound and outbound adapters to connect a converged SDD core to real infrastructure — Phase 6 of the SDD workflow. Use this only after the system reports is_converged. Generates adapter files satisfying the Adapter Protocol, following the reference patterns (SqlitePersistenceAdapter, HttpInboundAdapter). Wires them into a ProductionRunner driver. Triggers include "scaffold adapters", "connect to HTTP", "add persistence", "sdd:adapt", "wire up the database", "now connect this to FastAPI".
---

# sdd:adapt — Scaffold I/O adapters for a converged core

You are doing Phase 6 of SDD: connecting the verified machine core to real infrastructure. Adapters are deliberately thin — they translate between external systems and `runner.fire(...)` calls, with **zero domain logic**. The machines built during simulation ship unchanged.

## When this skill fires

**Hard precondition:** the system must already be converged. If the user invokes this skill on an unconverged system, first run `/sdd:converge` and refuse to proceed until `is_converged: True`. SDD is built on convergence-before-adaptation; bypassing it defeats the whole methodology.

Trigger phrases: "scaffold adapters", "connect to HTTP", "add database persistence", "wire up FastAPI/Redis/queue", "make this production-ready", etc.

## What to do

### 1. Verify convergence

Read the most recent ConvergenceReport (or rerun convergence). If `is_converged` is False, stop:

> The core isn't converged yet. Adapter work would be premature. Run `/sdd:converge` first, then `/sdd:diagnose` for any failures, until convergence passes.

### 2. Inventory I/O needs

Ask the user (or extract from the project description):

**Inbound** (signals that should trigger transitions):
- HTTP endpoints? Which paths → which transitions?
- Webhooks from external services (payment processors, etc.)?
- Queue consumers? Which queues, which message types?
- Scheduled jobs (cron-driven timeouts)?
- CLI commands?

**Outbound** (events that should produce side effects):
- Database persistence? Which storage?
- Notifications (email, SMS, push)?
- External API calls (payment authorization, shipping)?
- Webhook emissions to partners?
- Audit logging?

Produce an inventory table:

| Direction | External system | Adapter file | Triggers / Listens to |
|---|---|---|---|
| Inbound | HTTP API | `adapters/inbound/http_api.py` | POST /orders/{id}/{transition} → runner.fire |
| Outbound | Persistence | `adapters/outbound/sqlite_persistence.py` | * (snapshot on every event) |
| Outbound | Email | `adapters/outbound/email_notifications.py` | order.completed, payment.failed |

Show the table; confirm with the user before scaffolding.

### 3. Scaffold each adapter

Every adapter satisfies the `Adapter` protocol from `sdd.adapters`:

```python
class MyAdapter:
    def attach(self, runner: "ProductionRunner") -> None: ...
    def startup(self) -> None: ...    # optional
    def shutdown(self) -> None: ...   # optional
```

Required: `attach(runner)`. Optional: `startup()` / `shutdown()` for resource lifecycle. The protocol is `runtime_checkable`, so duck-typing is enough — no inheritance required.

#### Outbound adapter template

```python
"""<{external system}> outbound adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.adapters import ProductionRunner
    from sdd.events import Event


class {ExternalSystem}Adapter:
    """Subscribes to <events> and performs <external operations>."""

    def __init__(self, <config>) -> None:
        self._runner: "ProductionRunner | None" = None
        # ... store config

    def attach(self, runner: "ProductionRunner") -> None:
        self._runner = runner
        # Subscribe to specific events
        runner.event_bus.subscribe("domain.event_name", self._on_event)
        # OR subscribe to a pattern
        runner.event_bus.subscribe_pattern("domain.*", self._on_any_domain_event)

    def startup(self) -> None:
        # Open connections, start threads, etc.
        ...

    def shutdown(self) -> None:
        # Close resources in reverse order of acquisition
        ...

    def _on_event(self, event: "Event") -> None:
        # Perform the external operation
        # NO domain logic — just translate event.payload to external call
        ...
```

#### Inbound adapter template

```python
"""<{external system}> inbound adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.adapters import ProductionRunner


class {ExternalSystem}InboundAdapter:
    """Translates <external signals> into runner.fire() calls."""

    def __init__(self, <config>) -> None:
        self._runner: "ProductionRunner | None" = None
        # ... store config

    def attach(self, runner: "ProductionRunner") -> None:
        self._runner = runner

    def startup(self) -> None:
        # Start the listener (HTTP server, queue consumer, etc.)
        ...

    def shutdown(self) -> None:
        # Stop the listener cleanly
        ...

    def _handle_external_signal(self, raw_input) -> dict:
        """Three steps, in order:
        1. Deserialize raw_input
        2. Resolve which machine instance + transition
        3. runner.fire(...) and return serialized StepResult
        """
        # 1. Deserialize
        data = parse_input(raw_input)

        # 2. Resolve — extract instance_id, transition name from the path/message
        instance_id = data["id"]
        machine_class = self._runner.get_machine_class(data["machine"])
        transition = data["transition"]

        # 3. Fire
        result = self._runner.fire(
            instance_id, machine_class, transition, **data["kwargs"]
        )
        return serialize_step_result(result)
```

### 4. Wire up a production driver

Create or extend `prod.py`:

```python
"""Production driver."""
from sdd.adapters import ProductionRunner
from adapters.outbound.sqlite_persistence import SqlitePersistenceAdapter
from adapters.inbound.http_api import HttpInboundAdapter

# Import every machine
from machines.order_lifecycle import OrderLifecycle
from machines.payment_flow import PaymentFlow


def main():
    runner = ProductionRunner()
    runner.register(OrderLifecycle)
    runner.register(PaymentFlow)
    runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))
    runner.register_resolver(PaymentFlow, lambda e: e.payload.get("order_id"))

    runner.attach(SqlitePersistenceAdapter("orders.db"))
    runner.attach(HttpInboundAdapter(host="0.0.0.0", port=8080))

    with runner:  # startup() / shutdown() handled automatically
        # If using a long-running inbound adapter (HTTP, queue), block here
        import time
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("shutting down")


if __name__ == "__main__":
    main()
```

### 5. Write an integration test

The integration test replays scenarios from `scenarios/` against the production runner with adapters attached, and asserts the same final states. Pattern:

```python
def test_e2e_matches_simulation(tmp_path):
    db = str(tmp_path / "test.db")
    runner, http, _ = _build_runner(db)
    with runner:
        # Drive the happy path via the inbound adapter (HTTP, queue, etc.)
        # ...
        assert runner.get(OrderLifecycle, "ord_1").current_state == "completed"
```

Plus a **process-boundary survival test** if persistence is involved: tear down the runner mid-scenario, spin up a fresh one against the same DB, complete the scenario. Confirms `snapshot()/restore()` works.

See `tests/test_integration_e2e.py` in the framework repo for a worked example.

### 6. Suggest the next step

> Adapters scaffolded and integration-tested. The core ran through `/sdd:converge` plus the adapters' tests are green — the system is production-ready. Deploy with `python prod.py`, or run `/sdd:converge` again whenever machines change.

## The four adapter design rules (from docs/06)

Surface these to the user when reviewing their adapter code:

1. **No domain logic.** No `if` statements about business rules inside an adapter. "Whether an order can be cancelled" is a machine guard, not an adapter conditional.
2. **Replaceable.** Swapping Postgres for SQLite or HTTP for a CLI must not change the machine core's behavior.
3. **Fail gracefully.** Adapter failures (DB unavailable, email service down) must not corrupt machine state. Handle retries / circuit breakers inside the adapter.
4. **Independently testable.** Each adapter tests against mocks or fakes for its external system. Domain correctness is proven by simulation; adapter tests only verify the plumbing.

If the user's adapter draft violates rule 1, redirect — that business rule belongs in a machine.

## Reference implementations

The framework repo at `github.com/PSthelyBlog/simulation-driven-development` ships two reference adapters:

- `adapters/outbound/sqlite_persistence.py` — bidirectional persistence (~95 lines, stdlib only)
- `adapters/inbound/http_api.py` — HTTP via stdlib `http.server` (~115 lines, no deps)

Use these as templates. They demonstrate both the protocol satisfied by class duck-typing and the `runner.attach(...)` / `with runner:` lifecycle.

## What not to do

- Do not write adapters before convergence. The whole point of SDD is that the core is proven before infrastructure exists.
- Do not put business rules in adapters. If you're writing `if payment.status == "captured"` in an outbound adapter, that decision belongs in a machine guard.
- Do not skip the integration test. The handoff from simulation to production must be empirically demonstrated, not assumed.
- Do not add infrastructure imports to machine files in order to make an adapter easier. The dependency arrow points inward only.
- Do not couple two adapters. Each adapter knows only the runner; they don't reference each other.
