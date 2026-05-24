# SDD Framework Implementation Plan

## Overview

This document captures the implementation strategy for the Simulation-Driven Development framework core, derived from a multi-perspective brainstorm weighing speed, correctness, and developer experience.

## Guiding Principles

### Non-Negotiable Architectural Decisions

1. **Guard/effect separation**: Guards read context (read-only copy), never mutate. Side effects (`on_enter_*`, `on_exit_*`) may mutate and emit events.

2. **Append-only event log**: `EventBus` maintains an immutable, ordered log. Events are frozen dataclasses. No deletions, no mutations.

3. **Explicit failure reasons**: `TransitionResult` returns one of three failure types: `unknown_transition`, `invalid_source_state`, `no_guard_passed`. No exceptions for flow control.

4. **Zero infrastructure imports**: Machine files import only `sdd.protocol` and Python stdlib. This is enforced by design, not convention.

### Deferred Decisions

- **Threading**: Single-threaded simulation first. Design allows adding `Lock` later if needed.
- **Schema validation**: Event payload schemas validated after first multi-machine scenario proves the need.
- **Static analysis**: Guard purity checking deferred until protocol is stable.

## Implementation Phases

### Phase 1: Foundation (protocol.py + events.py)

**Goal**: Core building blocks that everything else depends on.

#### sdd/protocol.py

```
Components:
├── State                    # Descriptor for declaring states
│   ├── initial: bool
│   └── final: bool
├── TransitionResult         # Frozen dataclass
│   ├── success: bool
│   ├── source: str
│   ├── target: str
│   ├── transition: str
│   ├── failure_reason: str | None
│   └── events_emitted: list[Event]
└── StateMachine             # Base class
    ├── context: dict        # Working data (read-only property returns copy)
    ├── current_state: str
    ├── is_final: bool
    ├── available_transitions: list[str]
    ├── history: list[TransitionRecord]
    ├── fire(transition, **kwargs) -> TransitionResult
    ├── emit(name, payload) -> None
    ├── snapshot() -> dict
    └── restore(snapshot, event_bus) -> StateMachine  # classmethod
```

**Key implementation details**:
- `State` uses descriptor protocol (`__set_name__`, `__get__`)
- Transitions declared as `source.to(target)` with `|` for branching
- Guards discovered by naming convention: `guard_{transition}_to_{target}`
- Side effects discovered by naming convention: `on_enter_{state}`, `on_exit_{state}`
- `context` property returns `dict.copy()` to prevent external mutation

#### sdd/events.py

```
Components:
├── Event                    # Frozen dataclass
│   ├── name: str
│   ├── payload: dict
│   ├── source_machine: str
│   ├── source_instance: str
│   ├── timestamp: float
│   └── correlation_id: str
├── EventBus
│   ├── emit(event) -> None
│   ├── subscribe(event_name, handler) -> None
│   ├── subscribe_pattern(pattern, handler) -> None
│   └── log: list[Event]     # Read-only, append-only
└── EventLog                 # Query interface
    ├── filter(name, source_machine, source_instance, correlation_id) -> EventLog
    ├── after(timestamp) -> EventLog
    ├── before(timestamp) -> EventLog
    └── unique_instances(machine_name) -> set[str]
```

**Key implementation details**:
- `Event` is `@dataclass(frozen=True)`
- `EventBus._log` is private; `log` property returns copy
- Subscriptions stored as `dict[str, list[Callable]]`
- Pattern subscriptions use `fnmatch` for glob matching

**Exit criteria**: Can instantiate a `StateMachine` subclass, fire transitions, emit events, query the log.

---

### Phase 2: Reference Machine

**Goal**: Validate protocol API with a real domain machine before building runner.

#### machines/order_lifecycle.py

Implement the `OrderLifecycle` machine from the documentation:

```
States: created (initial) → validated → fulfilled → completed (final)
                                                  ↘ cancelled (final)

Transitions:
- validate: created → validated | cancelled
- fulfill: validated → fulfilled
- complete: fulfilled → completed
- cancel: (created | validated | fulfilled) → cancelled

Events emitted:
- order.validated {order_id, items, customer_id}
- order.fulfilled {order_id, warehouse_id}
- order.completed {order_id}
- order.cancelled {order_id, reason}

Context fields:
- order_id, customer_id, items, cancellation_reason
```

**Key implementation details**:
- Validates that `State` descriptor works correctly
- Tests guarded transitions (`validate` → two possible targets)
- Confirms `emit()` produces correct `Event` objects
- Exercises `snapshot()` and `restore()`

**Exit criteria**: `OrderLifecycle` can be instantiated, transitions fire correctly, events appear in log.

---

### Phase 3: Minimal Runner

**Goal**: Execute scenarios against machines without full convergence checking.

#### sdd/runner.py

```
Components:
├── SimulationRunner
│   ├── register(machine_class) -> None
│   ├── register_resolver(machine_class, resolver_fn) -> None
│   ├── create(machine_class, instance_id, context) -> StateMachine
│   ├── get(machine_class, instance_id) -> StateMachine | None
│   ├── fire(instance_id, machine_class, transition, **kwargs) -> StepResult
│   ├── reset() -> None
│   ├── machine_states() -> dict[(class, id), state]
│   ├── active_instances() -> list
│   ├── event_log -> EventLog
│   └── event_bus -> EventBus
├── StepResult               # Dataclass
│   ├── trigger: str
│   ├── transitions_fired: list[TransitionRecord]
│   ├── events_emitted: list[Event]
│   ├── cascade_depth: int
│   └── errors: list[StepError]
└── StepError                # Dataclass
    ├── machine: str
    ├── instance: str
    ├── transition: str
    ├── error_type: str
    └── message: str
```

**Key implementation details**:
- Instance registry: `dict[(MachineClass, instance_id), StateMachine]`
- Event delivery: depth-first, synchronous
- Cascade protection: max depth 20, error on exceeded
- Subscriptions wired from `machine_class.subscriptions()` at registration

**Exit criteria**: Can `create()` an OrderLifecycle, `fire()` transitions, observe events cascade to subscribers.

---

### Phase 4: Scenario Parser

**Goal**: Execute YAML scenarios programmatically.

#### sdd/scenario.py

```
Components:
├── Step                     # Union type for step kinds
│   ├── CreateStep(machine, instance_id, context)
│   ├── FireStep(machine, instance_id, transition, args, expect_failure)
│   ├── AdvanceTimeStep(duration)
│   └── AssertStep(states, events_emitted, events_not_emitted, context)
├── Scenario                 # Dataclass
│   ├── name: str
│   ├── narrative: str
│   ├── setup: list[Step]
│   ├── steps: list[Step]
│   └── expect: ExpectBlock
├── ScenarioParser
│   └── parse(yaml_path) -> Scenario
└── ScenarioRunner
    └── run(scenario, runner) -> ScenarioResult
```

**Key implementation details**:
- YAML parsing with `PyYAML` (standard library supplement, minimal dependency)
- Machine class resolution from string names via runner's registry
- Instance ID extraction from `MachineName("instance_id")` syntax
- `expect_failure` on FireStep inverts success condition

**Exit criteria**: Can parse `scenarios/happy_path_order.yaml` and execute it against runner.

---

### Phase 5: Structural Validation

**Goal**: Catch integration errors before scenarios run.

#### Additions to sdd/runner.py

```
SimulationRunner (extended):
├── check() -> StructuralReport
│   ├── reachability(machine_class) -> set[str]
│   ├── dead_states(machine_class) -> set[str]
│   ├── dead_letters() -> list[str]
│   ├── phantom_subscriptions() -> list[str]
│   └── schema_mismatches() -> list[SchemaMismatch]
└── converge(scenarios_dir, invariants_dir) -> ConvergenceReport
```

**Key implementation details**:
- Reachability: BFS from initial state through all transitions
- Dead letters: events emitted but not in any `subscriptions()`
- Phantom subscriptions: subscriptions to events no machine emits
- Schema checking deferred to Phase 6

**Exit criteria**: `runner.check()` returns report; dead states and dead letters detected.

---

### Phase 6: Invariant Checking

**Goal**: Cross-machine property validation.

#### sdd/invariants.py

```
Components:
├── Invariant                # Protocol/interface
│   └── check(log: EventLog, states: dict) -> InvariantResult
├── InvariantResult
│   ├── passed: bool
│   ├── invariant_name: str
│   └── violation_details: str | None
└── load_invariants(directory) -> list[Invariant]
```

**Key implementation details**:
- Invariants are Python functions with signature `(log, states) -> None` that raise `AssertionError` on violation
- Loaded dynamically from `invariants/*.py`
- Run after each scenario completes
- Results aggregated in convergence report

**Exit criteria**: Can define invariant, run scenarios, detect violations.

---

## File Structure After Implementation

```
sdd/
├── __init__.py
├── protocol.py      # State, StateMachine, TransitionResult
├── events.py        # Event, EventBus, EventLog
├── runner.py        # SimulationRunner, StepResult, StepError
├── scenario.py      # Step types, Scenario, ScenarioParser, ScenarioRunner
└── invariants.py    # Invariant loading and checking

machines/
└── order_lifecycle.py   # Reference implementation

scenarios/
└── happy_path_order.yaml

invariants/
└── order_invariants.py  # payment_before_completion, etc.
```

## Milestones

| Milestone | Deliverable | Validation |
|-----------|-------------|------------|
| M1 | `protocol.py` + `events.py` | Unit tests pass |
| M2 | `order_lifecycle.py` reference machine | Manual transitions work |
| M3 | Minimal `runner.py` | OrderLifecycle runs through runner |
| M4 | `scenario.py` parser | `happy_path_order.yaml` executes |
| M5 | Structural validation | `runner.check()` detects dead letters |
| M6 | Invariant checking | Cross-machine invariants enforced |

## Trade-offs Accepted

1. **Refactoring expected**: Runner API will evolve as real scenarios expose friction. This is discovery, not failure.

2. **Minimal dependencies**: Only `PyYAML` beyond stdlib. No dataclasses backport (Python 3.7+), no attrs, no pydantic.

3. **No concurrency initially**: Single-threaded deterministic execution. Threading support can be added to EventBus later without changing machine code.

4. **Schema validation deferred**: Event payload schemas enforced after multi-machine scenarios prove the need. Avoids premature abstraction.

## Next Action

Begin Phase 1: Implement `sdd/protocol.py` with `State`, `StateMachine`, and `TransitionResult`.
