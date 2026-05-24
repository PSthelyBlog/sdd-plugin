---
name: sdd-converge
description: Assemble registered machines, run all three convergence layers (structural + scenarios + invariants), surface coverage, and drive the fix loop until the system reports is_converged. Phases 2-5 of the SDD workflow. Use when machines have been authored and the user wants to verify the system end-to-end, or when iterating on a fix and needing a regression check. Triggers include "run convergence", "check convergence", "sdd:converge", "does the system converge?", "what's the convergence status?".
---

# sdd:converge — Run all three convergence layers

You are doing Phases 2 (assembly), 4 (simulation loop), and 5 (invariant verification) of the SDD workflow in one pass. The goal: a `ConvergenceReport` with `is_converged == True`. If anything fails, hand off to `sdd:diagnose` or apply a targeted fix and rerun.

## When this skill fires

- Machines are written (one or more files in `machines/`)
- The user wants to verify the system, or has just made a change and wants regression
- Optionally: scenarios exist in `scenarios/` and invariants in `invariants/`

If no scenarios exist yet, you should also propose them — see "Scenario Development" below.

## What to do

### 1. Build a driver script (or invoke inline)

Write or update `run.py` in the project root:

```python
"""Drive convergence."""
import sys
from pathlib import Path

# Discover and register all machines
from sdd.runner import SimulationRunner
from sdd.scenario import ScenarioParser
from sdd.invariants import load_invariants

# Import every machine
from machines.order_lifecycle import OrderLifecycle
from machines.payment_flow import PaymentFlow
# ... one import per machine

def build_runner() -> SimulationRunner:
    runner = SimulationRunner()
    runner.register(OrderLifecycle)
    runner.register(PaymentFlow)
    # ... one register() per machine

    # Resolvers — one per machine that subscribes to any event
    runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))
    runner.register_resolver(PaymentFlow, lambda e: e.payload.get("order_id"))
    return runner

if __name__ == "__main__":
    runner = build_runner()
    scenarios = ScenarioParser().parse_directory("scenarios") if Path("scenarios").is_dir() else []
    invariants = load_invariants("invariants") if Path("invariants").is_dir() else []
    report = runner.converge(scenarios=scenarios, invariants=invariants)
    print(report.summary())
    sys.exit(0 if report.is_converged else 1)
```

### 2. Run convergence

```bash
python run.py
```

Examine the output. It will fall into one of these patterns:

### 3. Interpret the result

**Path A — `is_converged: True`.** Everything passes. Report to the user with a brief summary. Suggest the next step:

> System converged. Ready for I/O adaptation. Run `/sdd:adapt` to scaffold inbound/outbound adapters.

**Path B — Structural failure (Layer 1).** Fix before scenarios are meaningful.

Possible issues:
- `unreachable_states` — a State declared but no transition targets it. Either remove or add a transition.
- `terminal_states` — a non-final State with no path to any final state. Add a transition to a final state.
- `dead_letters` — events emitted but no machine subscribes. Two cases:
  - **Adapter-only event** (notifications, audit) — expected, no fix needed; classify in your report to the user.
  - **Missing subscription** — a downstream machine should react but doesn't. Add to that machine's `subscriptions()`.
- `phantom_subscriptions` — subscriptions to events no machine emits. Almost always a typo in `subscriptions()` or a missing emit.
- `guard_completeness_issues` — guarded transitions with no fallback. Remove the explicit guard from the last branch.

Fix and rerun.

**Path C — Scenario failure (Layer 2).** A specific scenario didn't reach its expected state, or a step errored.

Read `result.failure_reason` and the relevant `step_results[i].errors`:
- `unknown_transition` — typo or missing transition.
- `invalid_source_state` — the scenario fires a transition from a state where it can't fire. Either the scenario is wrong or a prior transition didn't fire.
- `no_guard_passed` — all guards on a guarded transition returned False. Check whether the scenario should have passed (guard too strict) or fail (scenario is testing a rejection but didn't set `expect_failure`).
- `schema_validation_failed` — emitter payload doesn't match `EVENT_SCHEMAS`. Fix the emitter or the schema.
- `instance_not_found` — usually a cascade didn't fire because of a routing failure. Check `step_results[i].routing_failures`.

`routing_failures` are the critical diagnostic when cascades break:
- `no_resolver_registered` — call `runner.register_resolver(MachineClass, ...)` in your driver.
- `resolver_returned_none` — the resolver couldn't find its key in the event payload. The payload's `event_payload_keys` is shown — compare to what the resolver reads.
- `resolver_raised` — the resolver itself threw. Fix the resolver lambda.

**Path D — Invariant violation (Layer 3).** A scenario passed structurally but breaks a cross-machine rule.

Read `result.invariant_results.failures`:
- The failure includes the violating instance ID and a description.
- The fix is usually one of: a missing guard, a missing subscription, an event emitted in the wrong order, or a missing transition.
- After fixing, rerun **all** scenarios, not just the failing one. Invariant fixes can affect other paths.

**Path E — Coverage incomplete.** Structural and scenarios pass, invariants pass, but some transitions/states/branches aren't exercised.

Look at `transition_coverage` and `state_coverage` per machine. Each missing entry needs a scenario that hits it. Generate one scenario per missing (transition, source, target) triple, especially the failure branches. Per `docs/05`, these are derived systematically — not from creativity.

### 4. Scenario development (if missing)

If `scenarios/` is empty, propose scenarios derived from three sources, per `docs/08-scenario-language.md`:

- **Happy path** per business flow. From the user's task description.
- **Failure path** per guarded branch. One scenario per `|` in transition declarations.
- **Timeout path** per `@timeout`. Use `advance_time` to fire each declared timeout.

Show the proposed list to the user before writing the YAML files.

### 5. After every fix: regression-replay

When you fix a machine, rerun **all** scenarios, not just the one that was failing. A change in one machine can break another through the event bus. The framework's regression bench is implicit — running all scenarios is the default mode.

## Useful patterns

### Inspecting the event log on a failure

```python
runner.event_log.filter(source_instance="ord_1")
# Returns all events for one instance — trace the cascade
runner.event_log.filter(name="order.validated")
# Returns all order.validated events across instances
```

### Step-level diagnosis

```python
result = runner.fire("ord_1", OrderLifecycle, "validate")
for record in result.transitions_fired:
    print(record.transition, record.source, "→", record.target)
for err in result.errors:
    print(err.error_type, err.message)
for rf in result.routing_failures:
    print(rf.event_name, "→", rf.target_machine, rf.reason)
```

## What not to do

- Do not skip layers. Structural before scenarios; scenarios before invariants. If structural is failing, scenarios will produce noisy false failures.
- Do not write adapter code in this skill. Convergence is about the core. `sdd:adapt` is later.
- Do not declare convergence based on a passing happy path alone. Coverage must be complete and invariants must pass.
- Do not edit scenarios to make them pass. Scenarios are acceptance criteria — if a scenario is wrong, fix the machine. Only rewrite the scenario if you genuinely got the expected behavior wrong (rare).
- Do not silence dead letters by removing the emit. They might be adapter-only events that just don't have an adapter yet.
