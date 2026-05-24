# Convergence Criteria

## Purpose

Convergence is the point at which Claude Code considers the state machine core correct and ready for I/O adaptation. Convergence is not a single check — it is three layers evaluated in order. Each layer must pass before the next is meaningful.

```
Layer 1: Structural Completeness ──▶ Layer 2: Scenario Coverage ──▶ Layer 3: Property Invariants
         (static analysis)                  (simulation)                  (cross-machine logic)
```

## Layer 1: Structural Completeness

Structural checks run against machine class definitions without creating instances or firing transitions. They verify that the machines are well-formed in isolation and correctly wired together.

### Per-Machine Checks

**Reachability.** Every declared state must be reachable from the initial state through some sequence of transitions. An unreachable state is dead code — it was declared but can never be entered. This is always a modeling error.

**Termination.** Every non-final state must have at least one path (possibly through other states) to a final state. A state with no path to any final state is a potential deadlock — a machine instance could become permanently stuck there.

**Guard completeness.** For every guarded transition (multiple targets), the guards must be exhaustive. Either every branch has an explicit guard, or the last branch has no guard (acting as a default fallback). A guarded transition where all branches have explicit guards and none is guaranteed to pass can result in a `no_guard_passed` failure at runtime.

**Transition naming.** Each transition name within a machine must be unique. Each state name within a machine must be unique.

### Cross-Machine Checks

**No dead letters.** Every event emitted by any machine (found by inspecting `on_enter_*` and `on_transition_*` methods for `self.emit()` calls) must have at least one subscriber across all registered machines, or be explicitly marked as adapter-only (consumed by outbound adapters, not other machines).

**No phantom subscriptions.** Every event listed in any machine's `subscriptions()` must be emitted by at least one other machine. A subscription to a non-existent event means a machine is waiting for something that will never happen.

**Schema agreement.** For every event, the payload schema declared by the emitter must be compatible with the fields accessed by all subscribers. If `OrderLifecycle` emits `order.validated` with `{"order_id", "items"}` and `InventoryState` expects `{"order_id", "warehouse_id"}` from that event, there is a schema mismatch.

### Resolution

Structural failures are reported to Claude Code with specific diagnostics:

```
STRUCTURAL FAILURE: Unreachable state
  Machine: PaymentFlow
  State: "refunded"
  Reason: No transition targets "refunded" from any reachable state
  Suggestion: Add a transition from "captured" to "refunded"
```

Claude Code directs subagents to fix structural issues before proceeding. No scenarios run until all structural checks pass.

## Layer 2: Scenario Coverage

Scenario checks verify that the system **behaves correctly** under defined usage patterns. They require running the simulation.

### Scenario Sources

Scenarios come from three sources:

**User-derived scenarios.** Claude Code translates the user's original task description into concrete scenarios. "Build an order processing system" yields scenarios like:
- Happy path: order created, validated, paid, fulfilled, completed
- Payment failure: order created, validated, payment fails, order cancelled
- Cancellation: order created, validated, user cancels before payment

**Failure scenarios.** For every guarded transition with a failure branch, Claude Code generates a scenario that forces the failure path. These are systematic, not creative — one scenario per failure branch.

**Timeout scenarios.** For every time-based transition, Claude Code generates a scenario where the timeout fires and verifies the system handles it correctly.

### Coverage Requirements

**Transition coverage.** Every transition in every machine must be exercised by at least one scenario. This is the minimum bar. If a transition has never fired during simulation, there is no evidence it works.

**State coverage.** Every state in every machine must be entered by at least one scenario. This is usually implied by transition coverage but is checked independently.

**Branch coverage.** Every branch of every guarded transition must be exercised. If `validate` can go to either `validated` or `cancelled`, both paths must be taken across the scenario set.

**Event path coverage.** Every event subscription must be triggered at least once. If `InventoryState` subscribes to `order.validated`, at least one scenario must produce that event and verify the inventory machine responds correctly.

### Scenario Assertions

Each scenario defines expected outcomes checked after execution:

```yaml
scenario: happy_path_order
steps:
  - create: OrderLifecycle("ord_1", {order_id: "ord_1", customer_id: "c1", items: [{sku: "A"}]})
  - fire: OrderLifecycle("ord_1").validate
  - fire: PaymentFlow("ord_1").authorize(amount: 29.99)
  - fire: PaymentFlow("ord_1").capture
  - fire: OrderLifecycle("ord_1").fulfill
  - fire: OrderLifecycle("ord_1").complete
expect:
  states:
    OrderLifecycle("ord_1"): completed
    PaymentFlow("ord_1"): captured
    InventoryState("ord_1"): allocated
  events_emitted:
    - order.validated
    - payment.authorized
    - payment.captured
    - order.fulfilled
    - order.completed
  events_not_emitted:
    - order.cancelled
    - payment.failed
```

### Resolution

Scenario failures indicate one of three things:

1. **Machine logic is wrong.** A guard rejects valid input, a transition is missing, or a side effect emits the wrong event. Claude Code diagnoses the root cause from the `StepResult` and directs a fix.

2. **The scenario is unrealistic.** The expected outcome doesn't match how the machines should actually behave. Claude Code adjusts the scenario.

3. **A missing machine or transition.** The scenario requires behavior that hasn't been modeled yet. Claude Code identifies the gap and directs a subagent to add the missing piece.

Claude Code distinguishes these cases by examining the failure point: did a transition fail? Did a machine reach an unexpected state? Did an event not propagate?

## Layer 3: Property Invariants

Property invariants are rules that must hold **across** machines. No single machine can enforce them alone — they are emergent properties of the system's event-driven interactions.

### Invariant Definition

Invariants are expressed as assertions over the event log and machine states after any scenario execution:

```python
def invariant_payment_before_completion(log, states):
    """An order cannot complete unless its payment was captured."""
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "completed":
            payment_captured = log.filter(
                name="payment.captured",
                source_instance=instance,
            )
            assert len(payment_captured) > 0, (
                f"Order {instance} completed without payment capture"
            )

def invariant_no_double_capture(log, states):
    """Payment capture must occur at most once per order."""
    for instance in log.unique_instances("PaymentFlow"):
        captures = log.filter(
            name="payment.captured",
            source_instance=instance,
        )
        assert len(captures) <= 1, (
            f"Payment {instance} captured {len(captures)} times"
        )

def invariant_cancellation_releases_inventory(log, states):
    """If an order is cancelled after inventory reservation,
    inventory must be released."""
    for instance in log.unique_instances("OrderLifecycle"):
        was_cancelled = log.filter(
            name="order.cancelled", source_instance=instance
        )
        was_reserved = log.filter(
            name="inventory.reserved", source_instance=instance
        )
        if was_cancelled and was_reserved:
            was_released = log.filter(
                name="inventory.released", source_instance=instance
            )
            assert len(was_released) > 0, (
                f"Order {instance} cancelled after reservation "
                f"but inventory not released"
            )
```

### Invariant Sources

**Claude Code proposes invariants** based on its understanding of the task domain. The user's request "build an order processing system" implies invariants like "you can't complete an order without payment" even if the user never stated this explicitly.

**The user can declare invariants** directly as acceptance criteria: "an order must never be fulfilled without sufficient inventory."

**Invariants emerge from simulation.** When Claude Code observes surprising behavior during scenario runs — an order completing before payment, inventory going negative — it codifies the expected behavior as a new invariant.

### Invariant Evaluation

Every invariant is checked after every scenario execution. An invariant violation means the system permits a sequence of transitions that violates a domain rule.

Invariant violations are the **highest-priority fix** because they indicate that individually correct machines produce collectively incorrect behavior. The fix is usually a missing guard, a missing event subscription, or a missing transition.

### Resolution

When an invariant fails, Claude Code has a clear diagnostic path:

1. Identify the scenario that triggered the violation.
2. Examine the event log to find the specific moment the invariant was breached.
3. Determine which machine should have prevented the invalid state — usually by adding a guard that checks for a prerequisite event.
4. Direct the subagent to add the guard, rerun all scenarios, and recheck all invariants.

## Convergence Report

When all three layers run, `runner.converge()` returns a `ConvergenceReport` aggregating the results. Its `summary()` method renders the polished output below:

```
CONVERGENCE REPORT
==================

Structural Analysis: PASS
  - 3 machines, 12 states, 15 transitions
  - Unreachable states: 0
  - Terminal (deadlock) states: 0
  - Dead letters: 0
  - Phantom subscriptions: 0
  - Guard-completeness gaps: 0

Scenario Coverage: PASS
  - 7/7 scenarios passed
  - OrderLifecycle: 15/15 transitions (100%)
  - OrderLifecycle: 12/12 states (100%)
  - Event subscriptions: 8/8 (100%)

Property Invariants: PASS
  - 5/5 invariant checks passed

System is converged. Ready for I/O adaptation.
```

`ConvergenceReport` exposes structured fields for programmatic use:

| Property | Type | Meaning |
|---|---|---|
| `structural` | `StructuralReport \| None` | Layer-1 result |
| `scenario_results` | `list[ScenarioResult]` | Layer-2 per-scenario outcome |
| `transition_coverage` / `state_coverage` / `event_path_coverage` | `dict` | Coverage breakdowns per machine |
| `invariant_results` | `list[InvariantReport]` | Layer-3 per-scenario invariants |
| `structural_passed` / `scenarios_passed` / `invariants_passed` / `coverage_complete` | `bool` | Layer-level verdicts |
| `is_converged` | `bool` | True iff all four above are True |

This report is presented to the user as evidence that the domain logic is correct before any infrastructure code is written. It is also the natural input to a "go/no-go" gate before kicking off Phase 6 (I/O adaptation).
