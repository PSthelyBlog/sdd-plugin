# Simulation Runner

## Purpose

The simulation runner is the environment in which Claude Code operates state machines. It manages machine instances, routes events, executes transitions, and exposes the full system state for inspection. It is **not** throwaway tooling — it persists as the project's domain-level test harness indefinitely.

## Runner Lifecycle

```
Initialize ──▶ Load Machines ──▶ Run Scenario ──▶ Inspect ──▶ Report
                                      │                         │
                                      ◀────── (iterate) ────────┘
```

### Initialization

```python
runner = SimulationRunner()

# Register machine classes
runner.register(OrderLifecycle)
runner.register(PaymentFlow)
runner.register(InventoryState)

# Register instance resolvers
runner.register_resolver(OrderLifecycle, lambda e: e.payload["order_id"])
runner.register_resolver(PaymentFlow, lambda e: e.payload["order_id"])
runner.register_resolver(InventoryState, lambda e: e.payload["order_id"])
```

Registration tells the runner which machine types exist, how to resolve event-to-instance mappings, and wires up the event subscriptions declared by each machine class.

### Machine Instance Management

The runner maintains a registry of live machine instances, keyed by `(MachineClass, instance_id)`:

```python
# Explicit creation
order = runner.create(OrderLifecycle, "ord_123", context={
    "order_id": "ord_123",
    "customer_id": "cust_456",
    "items": [{"sku": "WIDGET", "qty": 2}],
})

# Implicit creation via event routing
# If an event arrives for an instance that doesn't exist,
# the runner creates it with context seeded from the event payload.
```

Machines are never accessed directly during simulation. All interaction goes through the runner, which ensures events are properly routed and logged.

## Executing Transitions

### Direct Firing

Claude Code can fire a transition directly on a specific machine instance:

```python
result = runner.fire("ord_123", OrderLifecycle, "validate")
```

This is the primary mechanism during scenario execution. Claude Code steps through a scenario by firing transitions in sequence and examining results.

### Event-Driven Firing

When a machine emits an event, the runner handles delivery automatically:

1. Match the event name to subscriptions across all registered machine classes.
2. Resolve the target instance for each subscribing machine class.
3. Fire the declared transition on the target instance.
4. Collect results and any cascading events.
5. Repeat until the event queue is empty.

The runner returns a `StepResult` capturing everything that happened:

```python
@dataclass
class StepResult:
    trigger: str                          # What initiated this step
    transitions_fired: list[TransitionRecord]  # All transitions, in order
    events_emitted: list[Event]           # All events, in order
    cascade_depth: int                    # How deep the event chain went
    errors: list[StepError]               # Any failures during the step
```

## Scenario Execution

A scenario is a sequence of stimuli applied to the system. Scenarios are authored in YAML (see `docs/08-scenario-language.md`), parsed into `Scenario` objects, and executed by `ScenarioRunner`:

```python
from sdd.scenario import ScenarioParser, ScenarioRunner

scenarios = ScenarioParser().parse_directory("scenarios")
sr = ScenarioRunner(runner)
results = sr.run_all(scenarios)  # list[ScenarioResult]
```

`run_all` accepts an optional `invariants=` keyword to also check property invariants after each scenario. Between each step inside a scenario, the runner drains the event queue, so cascading effects from one step resolve before the next step begins.

After execution, Claude Code inspects `results` to check whether machines reached expected states, events were emitted correctly, and no errors occurred. Each `ScenarioResult` carries `step_results` (one per fire step), `failure_step` / `failure_reason` if any, `final_states`, `events_emitted`, and `invariant_results` if invariants were checked.

### Convergence: run everything at once

`runner.converge()` runs all three layers (structural + scenarios + invariants) plus computes coverage and returns a single `ConvergenceReport`:

```python
from sdd.invariants import load_invariants

report = runner.converge(
    scenarios=scenarios,
    invariants=load_invariants("invariants"),
)
print(report.summary())     # polished output, docs/05 format
print(report.is_converged)  # True only when all three layers pass + coverage complete
```

See `docs/05-convergence-criteria.md` for the report shape and the meaning of `is_converged`.

## Inspection API

The runner exposes the full system state to Claude Code at any point during or after scenario execution.

### System-Level Inspection

```python
runner.machine_states()
# Returns: {
#   (OrderLifecycle, "ord_123"): "completed",
#   (PaymentFlow, "ord_123"): "captured",
#   (InventoryState, "ord_123"): "allocated",
# }

runner.active_instances()
# All non-final machine instances

runner.final_instances()
# All machine instances in a final state

runner.event_log
# Complete event log with query support
```

### Instance-Level Inspection

```python
order = runner.get(OrderLifecycle, "ord_123")
order.current_state          # "completed"
order.history                # [TransitionRecord, ...]
order.context                # {"order_id": "ord_123", ...}
order.available_transitions  # [] (final state)
```

### Structural Inspection

These queries operate on machine **classes**, not instances, and are used for static analysis before scenarios run:

```python
runner.reachability(OrderLifecycle)
# Returns which states are reachable from initial state

runner.dead_states(OrderLifecycle)
# States that are declared but unreachable

runner.dead_letters()
# Events emitted by some machine but subscribed to by none

runner.unhandled_events()
# Events subscribed to by some machine but emitted by none

runner.transition_coverage(scenario_results)
# Which transitions were exercised by a scenario run
```

## Reset and Isolation

Each scenario runs in a clean environment:

```python
runner.reset()
# Destroys all machine instances, clears event log,
# retains machine class registrations and resolvers
```

Scenarios do not share state. This guarantees that scenario results are independent and reproducible.

## Determinism

The simulation runner is fully deterministic. Given the same machine registrations, the same scenario steps, and the same guard logic, the output is identical across runs. There is no concurrency, no randomness, and no external dependency.

This is critical for Claude Code's operation loop. When Claude Code modifies a machine and reruns a scenario, any difference in results is attributable to the code change, not environmental factors.

## Error Handling

The runner does not crash on transition failures. It records them and continues:

```python
@dataclass
class StepError:
    step_index: int
    machine: str
    instance: str
    transition: str
    error_type: str          # "unknown_transition", "invalid_source_state", "no_guard_passed"
    message: str
    machine_state_at_error: str
```

Claude Code examines errors after a scenario run to determine whether they represent bugs (a transition should have been possible but wasn't) or expected behavior (testing a failure path).

## Temporal Simulation

For timeout and deadline logic, the runner provides a virtual clock:

```python
runner.advance_time(hours=24)
```

This triggers any time-based transitions registered on machines (e.g., "if payment is still `pending` after 24 hours, transition to `expired`"). The virtual clock is monotonic and controlled entirely by Claude Code — no wall-clock dependency.

Time-based transitions are declared in machines using a `timeout` decorator:

```python
class PaymentFlow(StateMachine):
    pending = State(initial=True)
    expired = State(final=True)

    expire = pending.to(expired)

    @timeout(expire, hours=24)
    def payment_deadline(self):
        """Payment must be authorized within 24 hours."""
        self.context["expiry_reason"] = "authorization_timeout"
```

The runner evaluates all pending timeouts whenever `advance_time` is called.
