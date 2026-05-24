# Implementation Notes

## Session 1: Phase 1 Foundation (PR #2)

### What Was Implemented

**sdd/protocol.py**
- `State` descriptor with `initial` and `final` flags
- `StateGroup` for multi-source transitions (e.g., `(state1 | state2).to(target)`)
- `TransitionBranch` representing a single source→target path
- `Transition` supporting branching with `|` operator
- `TransitionResult` frozen dataclass with explicit failure reasons
- `TransitionRecord` for history tracking
- `StateMachine` base class with full protocol implementation
- `StateMachineMeta` metaclass for collecting states/transitions and validation

**sdd/events.py**
- `Event` frozen dataclass
- `EventBus` with exact and pattern-based subscriptions
- `EventLog` query interface with chainable filters
- Cascade depth limiting (default: 20)

### Key Design Decision: Deferred Name Resolution

**Problem encountered:** When Python executes the class body, `state.to(target)` is called *before* `__set_name__` runs. This means state names are empty strings at transition creation time.

**Solution:** Store `State` object references in `TransitionBranch` and `StateGroup`, then resolve names to strings in the metaclass after all `__set_name__` calls complete.

```python
# TransitionBranch stores State references, not strings
def __init__(self, source: "State", target: "State") -> None:
    self._source_state = source
    self._target_state = target
    self.source: str = ""  # Resolved by metaclass
    self.target: str = ""  # Resolved by metaclass

def _resolve_names(self) -> None:
    self.source = self._source_state._name
    self.target = self._target_state._name
```

The metaclass calls `transition._resolve_names()` for each transition after collecting them.

### Test Coverage

69 unit tests covering:
- State descriptor behavior
- Machine validation (initial/final state requirements)
- Transition firing (success, unknown, invalid source, no guard)
- Guard discovery and invocation
- Side effect execution order (exit → transition → enter)
- Event emission and bus delivery
- Introspection API
- Serialization (snapshot/restore)
- EventLog filtering and chaining

### Files Added

```
sdd/
├── __init__.py      # Package exports
├── events.py        # Event, EventBus, EventLog
└── protocol.py      # State, Transition, StateMachine, etc.

tests/
├── __init__.py
├── test_events.py   # 27 tests
└── test_protocol.py # 42 tests

.gitignore           # Python/venv/IDE ignores
pyproject.toml       # Project config, pytest settings
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

### What's Next

See Session 2 notes below.

### Notes for Future Sessions

1. **Virtual environment** is at `.venv/` (not `venv/`)

2. **Zero infrastructure imports** - Machine files must only import from `sdd.protocol` and Python stdlib. Verify with:
   ```bash
   grep -E "^import|^from" sdd/*.py | grep -v "from __future__" | grep -v "from sdd" | grep -v "from dataclasses" | grep -v "from typing" | grep -v "import time" | grep -v "import fnmatch"
   ```

3. **Guard naming convention**: `guard_{transition}_to_{target}` - if no guard exists for a branch, it defaults to `True` (always allowed)

4. **Side effect naming convention**: `on_enter_{state}`, `on_exit_{state}`, `on_transition_{transition}`

5. **Context updates**: kwargs passed to `fire()` are merged into context *before* guards evaluate

6. **Event delivery**: Events are collected during transition, then delivered to EventBus after all side effects complete

7. **The `subscriptions()` classmethod** is defined but not yet wired up - that's for the SimulationRunner in Phase 3

---

## Session 2: Phase 2 Reference Machine (PR #4)

### What Was Implemented

**machines/order_lifecycle.py**
- `OrderLifecycle` state machine as the reference implementation
- Validates the Phase 1 protocol API works correctly before building the runner

### Machine Specification

```
States: created (initial) → validated → fulfilled → completed (final)
                                                  ↘ cancelled (final)

Transitions:
- validate: created → validated | cancelled  (guarded branching)
- fulfill:  validated → fulfilled
- complete: fulfilled → completed
- cancel:   (created | validated | fulfilled) → cancelled  (multi-source)

Events:
- order.validated  {order_id, items, customer_id}
- order.fulfilled  {order_id, warehouse_id}
- order.completed  {order_id}
- order.cancelled  {order_id, reason}

Context fields:
- order_id, customer_id, items, warehouse_id, cancellation_reason
```

### Key Patterns Demonstrated

1. **Guarded branching transitions** - The `validate` transition can go to either `validated` or `cancelled` based on guards:
   ```python
   validate = created.to(validated) | created.to(cancelled)

   def guard_validate_to_validated(self, **kwargs) -> bool:
       items = self._context.get("items", [])
       return bool(items) and all(item.get("quantity", 0) > 0 for item in items)

   def guard_validate_to_cancelled(self, **kwargs) -> bool:
       return not self.guard_validate_to_validated(**kwargs)
   ```

2. **Multi-source transitions with StateGroup** - The `cancel` transition uses the `|` operator to accept multiple source states:
   ```python
   cancel = (created | validated | fulfilled).to(cancelled)
   ```

3. **Event emission in side effects** - Events are emitted in `on_enter_*` methods:
   ```python
   def on_enter_validated(self) -> None:
       self.emit("order.validated", {
           "order_id": self._context.get("order_id"),
           "items": self._context.get("items", []),
           "customer_id": self._context.get("customer_id"),
       })
   ```

4. **Guard that always passes** - For transitions that should always be allowed:
   ```python
   def guard_cancel_to_cancelled(self, **kwargs) -> bool:
       return True
   ```

### Test Coverage

29 tests in `tests/test_order_lifecycle.py` covering:
- Machine structure (states, transitions, branches)
- Happy path (created → validated → fulfilled → completed)
- Validation failure path (invalid items → cancelled)
- Cancellation from each allowed state
- Invalid transition handling (wrong source state, unknown transition)
- Snapshot/restore round-trip
- Context updates via kwargs
- History tracking

### Files Added

```
machines/
├── __init__.py          # Package exports
└── order_lifecycle.py   # OrderLifecycle state machine

tests/
└── test_order_lifecycle.py  # 29 tests
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v                      # All 98 tests
python -m pytest tests/test_order_lifecycle.py  # Just OrderLifecycle tests
```

### What's Next

**Phase 3: SimulationRunner** (`sdd/runner.py`) — GitHub Issue #5

See `docs/04-simulation-runner.md` for the authoritative specification.

Key components (aligned with docs):
```
SimulationRunner
├── register(machine_class) -> None
├── register_resolver(machine_class, resolver_fn) -> None  # From docs
├── create(machine_class, instance_id, context) -> StateMachine
├── get(machine_class, instance_id) -> StateMachine | None
├── fire(instance_id, machine_class, transition, **kwargs) -> StepResult
├── reset() -> None
├── machine_states() -> dict[(class, id), state]
├── active_instances() -> list[(class, id)]
├── final_instances() -> list[(class, id)]  # From docs
├── event_log -> EventLog
└── event_bus -> EventBus

StepResult
├── trigger: str
├── transitions_fired: list[TransitionRecord]
├── events_emitted: list[Event]
├── cascade_depth: int
└── errors: list[StepError]

StepError (from docs/04-simulation-runner.md)
├── step_index: int              # Position in cascade
├── machine: str
├── instance: str
├── transition: str
├── error_type: str
├── message: str
└── machine_state_at_error: str  # From docs
```

### Notes for Future Sessions

1. **OrderLifecycle is the integration test** - Use it to validate runner behavior. If the runner can operate OrderLifecycle correctly, it's ready for more complex machines.

2. **Subscriptions wiring** - The `subscriptions()` classmethod returns `dict[str, str]` mapping event names to transition names. The runner must:
   - Call `subscriptions()` at registration time
   - Subscribe handlers to the EventBus for each event
   - When an event arrives, find instances that should react and fire the mapped transition

3. **Instance resolution via resolvers** (from `docs/04-simulation-runner.md`):
   ```python
   runner.register_resolver(OrderLifecycle, lambda e: e.payload["order_id"])
   ```
   The resolver extracts an instance ID from the event payload. This is **required** — there is no default broadcast behavior.

4. **Implicit instance creation** (from `docs/04-simulation-runner.md`):
   > "If an event arrives for an instance that doesn't exist, the runner creates it with context seeded from the event payload."

5. **Event payload as kwargs** (from `docs/03-event-system.md`):
   > "When the bus delivers a matching event, the simulation runner locates the appropriate machine instance and fires the corresponding transition, passing the event payload as keyword arguments."

6. **Error handling** (from `docs/04-simulation-runner.md`):
   > "The runner does not crash on transition failures. It records them and continues."

7. **Cascade depth tracking** - The EventBus already has cascade protection (max 20). The runner's `StepResult` should report the actual cascade depth for observability.

8. **Deferred features**:
   - Phase 4: `run_scenario()` and YAML parsing
   - Phase 5: Structural validation (`reachability()`, `dead_letters()`, `dead_states()`)
   - Phase 6: Invariant checking

---

## Session 3: Phase 3 SimulationRunner (PR #6)

### What Was Implemented

**sdd/runner.py**
- `StepError` dataclass for recording transition failures
- `StepResult` dataclass for capturing step execution results
- `SimulationRunner` class with full specification

### SimulationRunner Specification

```
SimulationRunner
├── register(machine_class) -> None           # Registers class and wires subscriptions
├── register_resolver(machine_class, resolver) -> None  # Sets instance ID extraction
├── create(machine_class, instance_id, context) -> StateMachine  # Creates instance
├── get(machine_class, instance_id) -> StateMachine | None  # Retrieves instance
├── fire(instance_id, machine_class, transition, **kwargs) -> StepResult  # Fires transition
├── reset() -> None                           # Clears instances and log, keeps registrations
├── machine_states() -> dict[(class, id), state]  # All instance states
├── active_instances() -> list[(class, id)]   # Non-final instances
├── final_instances() -> list[(class, id)]    # Final instances
├── event_bus -> EventBus                     # The runner's event bus
└── event_log -> EventLog                     # Query interface to event log

StepResult
├── trigger: str                              # What initiated the step (e.g., "fire:validate")
├── transitions_fired: list[TransitionRecord] # All transitions, in order
├── events_emitted: list[Event]               # All events, in order
├── cascade_depth: int                        # How deep the event chain went
└── errors: list[StepError]                   # Any failures during the step

StepError
├── step_index: int                           # Position in cascade (0 = initial fire)
├── machine: str                              # Machine class name
├── instance: str                             # Instance ID
├── transition: str                           # Transition that failed
├── error_type: str                           # "unknown_transition" | "invalid_source_state" | "no_guard_passed"
├── message: str                              # Human-readable description
└── machine_state_at_error: str               # State when error occurred
```

### Key Behaviors Implemented

1. **Registration and Subscription Wiring**
   - `register()` stores the machine class and wires up event subscriptions
   - Calls `machine_class.subscriptions()` to get event→transition mapping
   - Subscribes handlers to EventBus for each declared event

2. **Instance Management**
   - `create()` instantiates machines with context, connects to event bus
   - `get()` retrieves existing instances by (class, id) key
   - **Implicit creation**: When event arrives for non-existent instance, creates it with context seeded from event payload

3. **Execution**
   - `fire()` triggers a transition and handles full event cascade
   - Collects all transitions fired, events emitted, and errors
   - **Does not crash on failures** — records errors in `StepResult.errors`

4. **Event-Driven Transitions**
   - Resolvers extract instance ID from event payload
   - Events cascade through subscribing machines automatically
   - Cascade depth is tracked in `StepResult`

5. **Reset Behavior**
   - Clears all instances and event log
   - Retains machine class registrations and resolvers

### Test Coverage

38 unit tests in `tests/test_runner.py` covering:
- Registration and resolver setup
- Instance creation and retrieval
- Direct transition firing via `fire()`
- Event-driven transition firing (subscription wiring)
- Cascade handling (event triggers event triggers event)
- Error capture (failed transitions don't crash)
- Reset behavior
- State inspection methods (machine_states, active_instances, final_instances)
- EventBus and EventLog property access

### Files Added

```
sdd/
└── runner.py           # SimulationRunner, StepResult, StepError

tests/
└── test_runner.py      # 38 tests
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v                  # All 136 tests
python -m pytest tests/test_runner.py -v    # Just runner tests (38)
```

### What's Next

**Phase 4: Scenario Parser** (`sdd/scenario.py`)

See `docs/08-scenario-language.md` for the authoritative specification.

Key components:
```
Step types:
├── CreateStep(machine, instance_id, context)
├── FireStep(machine, instance_id, transition, args, expect_failure)
├── AdvanceTimeStep(duration)
└── AssertStep(states, events_emitted, events_not_emitted, context)

Scenario
├── name: str
├── narrative: str
├── setup: list[Step]
├── steps: list[Step]
└── expect: ExpectBlock

ScenarioParser
└── parse(yaml_path) -> Scenario

ScenarioRunner
└── run(scenario, runner) -> ScenarioResult
```

### Notes for Future Sessions

1. **Parameter naming in _fire_internal** — Uses `target_instance_id` instead of `instance_id` to avoid conflicts with event payloads that may contain `instance_id` keys.

2. **Subscription handlers** — Created at registration time and capture machine_class and transition_name in closure.

3. **Cascade depth tracking** — Incremented on each internal fire, reported in StepResult.

4. **Error types** — The runner adds `instance_not_found` error type beyond the three defined in protocol (`unknown_transition`, `invalid_source_state`, `no_guard_passed`).

---

## Session 4: Phase 4 Scenario Parser (PR #7)

### What Was Implemented

**sdd/scenario.py**
- `CreateStep`, `FireStep`, `AdvanceTimeStep`, `AssertStep` dataclasses for step types
- `ExpectBlock` dataclass for final expectations
- `Scenario` dataclass for complete scenario definitions
- `ScenarioParser` class for parsing YAML scenario files
- `ScenarioRunner` class for executing scenarios against `SimulationRunner`
- `ScenarioResult` dataclass for execution results
- `ScenarioParseError` exception for parse errors with file location
- Helper functions `parse_instance_ref()` and `parse_fire_ref()` for parsing YAML syntax

**sdd/runner.py (extended)**
- Added `get_machine_class(name)` method for looking up registered machine classes by name

**scenarios/happy_path_order.yaml**
- Reference scenario file demonstrating the YAML syntax

### Scenario YAML Syntax

Machine instances are referenced using `MachineName("instance_id")`:

```yaml
# Create a machine instance
- create: OrderLifecycle("ord_1")
  context:
    order_id: "ord_1"

# Fire a transition
- fire: OrderLifecycle("ord_1").validate
  args:
    warehouse_id: "WH_EAST"

# Assert intermediate state
- assert:
    states:
      OrderLifecycle("ord_1"): validated
    events_emitted:
      - order.validated

# Time advancement (deferred - logs warning)
- advance_time: {days: 7, hours: 12}

# Test expected failure
- fire: OrderLifecycle("ord_1").fulfill
  expect_failure: invalid_source_state
```

### Key Behaviors Implemented

1. **Instance Reference Parsing**
   - `parse_instance_ref()` parses `MachineName("id")` → `(machine_name, instance_id)`
   - `parse_fire_ref()` parses `MachineName("id").transition` → `(machine, id, transition)`

2. **Step Execution**
   - `CreateStep`: Creates machine instance via `runner.create()`
   - `FireStep`: Fires transition via `runner.fire()`, handles `expect_failure`
   - `AdvanceTimeStep`: Logs warning (time advancement deferred to later phase)
   - `AssertStep`: Verifies states, events_emitted, events_not_emitted

3. **expect_failure Logic**
   - If `expect_failure` is set, the step passes only if the transition fails with that exact error type
   - If transition succeeds when failure was expected, the step fails
   - If failure type doesn't match expected, the step fails

4. **Scenario Execution Flow**
   1. Reset runner for clean state
   2. Execute setup steps
   3. Execute main steps in order
   4. If any step fails unexpectedly, halt and record failure
   5. After all steps, verify `expect` block
   6. Return `ScenarioResult`

5. **Error Handling**
   - Parse errors include file path and context
   - Unknown machine classes produce helpful error messages
   - Assertion failures report expected vs. actual values

### Test Coverage

51 tests in `tests/test_scenario.py` covering:
- Instance reference parsing (`MachineName("id")`)
- Fire reference parsing (`MachineName("id").transition`)
- YAML scenario parsing (all step types, expect block)
- Parse errors (file not found, invalid YAML, missing fields)
- Directory parsing (including subdirectories)
- Step execution (create, fire, assert, advance_time warning)
- expect_failure logic (success, wrong type, unexpected success)
- ExpectBlock validation (states, events_emitted, events_not_emitted)
- Full scenario execution with OrderLifecycle
- get_machine_class helper method

### Files Added/Modified

```
sdd/
├── __init__.py         # Added scenario module exports
└── scenario.py         # NEW: Scenario parser and runner

scenarios/
└── happy_path_order.yaml  # NEW: Reference scenario

tests/
└── test_scenario.py    # NEW: 51 tests
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v                  # All 187 tests
python -m pytest tests/test_scenario.py -v  # Just scenario tests (51)
```

### Dependencies Added

- `PyYAML>=6.0` — Added to `pyproject.toml` for YAML parsing

### What's Next

**Phase 5: Structural Validation** — GitHub Issue #8

Key components:
```
SimulationRunner (extended):
├── check() -> StructuralReport
│   ├── reachability(machine_class) -> set[str]
│   ├── dead_states(machine_class) -> set[str]
│   ├── dead_letters() -> list[str]
│   └── phantom_subscriptions() -> list[str]
└── converge(scenarios_dir, invariants_dir) -> ConvergenceReport
```

### Notes for Future Sessions

1. **Time advancement is deferred** — `AdvanceTimeStep` logs a warning and continues. Time-based transitions require virtual clock support in the runner.

2. **Context assertions are deferred** — `AssertStep.context` verification logs a warning. Implementing this requires deep comparison of nested dictionaries.

3. **Fragments are deferred** — Reusable scenario fragments with `use:` and `bind:` are specified in the docs but not implemented in Phase 4.

4. **Scenario metadata is deferred** — `category`, `priority`, `machines_involved`, `transitions_targeted` fields are documented but not parsed.

5. **The ScenarioRunner does not validate scenario structure** — It trusts that the parser produces valid scenarios. Invalid scenarios will produce runtime errors during execution.

---

## Session 5: Phase 5 Structural Validation (PR #10)

### What Was Implemented

**sdd/runner.py (extended)**
- `StructuralReport` dataclass for capturing structural analysis results
- `reachability(machine_class)` — BFS from initial state to find reachable states
- `dead_states(machine_class)` — states declared but not reachable
- `termination_issues(machine_class)` — non-final states with no path to final
- `_find_emitted_events(machine_class)` — AST parsing to find `self.emit()` calls
- `dead_letters()` — events emitted but not subscribed to
- `phantom_subscriptions()` — events subscribed to but not emitted
- `check()` — comprehensive structural check returning `StructuralReport`

### StructuralReport Specification

```python
@dataclass
class StructuralReport:
    machines_analyzed: list[str]           # Machine class names
    total_states: int                      # Sum of all states
    total_transitions: int                 # Sum of all transitions
    unreachable_states: dict[str, list[str]]  # machine -> dead states
    terminal_states: dict[str, list[str]]     # machine -> deadlock states
    dead_letters: list[str]                # emitted but not subscribed
    phantom_subscriptions: list[str]       # subscribed but not emitted

    @property
    def is_valid(self) -> bool:            # True if no issues found
```

### Key Implementation Details

1. **Reachability Analysis**
   - BFS from initial state through all transition branches
   - Returns set of reachable state names
   - Used as foundation for dead states detection

2. **Termination Analysis**
   - Reverse BFS from all final states
   - Finds states that can reach any final state
   - Non-final states not in this set are potential deadlocks

3. **Event Emission Detection**
   - Uses `inspect.getsource()` to get method source code
   - Uses `textwrap.dedent()` to handle class indentation
   - Uses `ast.parse()` and `ast.walk()` to find `self.emit()` calls
   - Extracts event name from first argument (string constant)
   - Scans `on_enter_*`, `on_exit_*`, `on_transition_*` methods

4. **Cross-Machine Analysis**
   - `dead_letters()`: Collects all emitted events, compares to all subscriptions
   - `phantom_subscriptions()`: Collects all subscriptions, compares to all emissions
   - Both return sorted lists for deterministic output

### Test Coverage

38 tests in `tests/test_structural.py` covering:
- StructuralReport dataclass and `is_valid` property
- Reachability analysis (normal, unreachable, multi-path, cyclic)
- Dead states detection
- Termination issues detection (deadlock states)
- Event emission detection via AST parsing
- Dead letters detection (single machine, multiple machines, partial)
- Phantom subscriptions detection
- Full `check()` method (valid system, various issues)
- Integration with OrderLifecycle reference machine

### Files Added/Modified

```
sdd/
├── __init__.py         # Added StructuralReport export
└── runner.py           # Added StructuralReport, structural validation methods

tests/
└── test_structural.py  # NEW: 38 tests
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v                    # All 225 tests
python -m pytest tests/test_structural.py -v  # Just structural tests (38)
```

### What's Next

### Notes for Future Sessions

1. **AST parsing for emit detection** requires `textwrap.dedent()` to properly handle method source code indentation. `inspect.cleandoc()` does not work correctly for class methods.

2. **The `check()` method** does not instantiate machines — all analysis is static, operating on class definitions.

3. **Schema validation is deferred**. The structural checks don't verify that event payloads match between emitters and subscribers.

4. **Guard completeness checking is deferred**. Detecting whether all guard branches are covered requires deeper static analysis.

5. **Dead letters in isolation are common**. The OrderLifecycle machine emits events but has no subscribers in the reference implementation. This is expected — downstream machines would subscribe in a real system.

6. **Test machines defined in test files** work with AST parsing because `inspect.getsource()` can retrieve their source code from the test file.

---

## Session 6: Phase 6 Invariant Checking (PR #12)

### What Was Implemented

**sdd/invariants.py**
- `InvariantResult` dataclass for single invariant check results
- `InvariantReport` dataclass for aggregated results with helper properties
- `check_invariant()` function to check a single invariant
- `check_invariants()` function to check multiple invariants and aggregate results
- `load_invariants()` function to dynamically load invariants from a directory
- `load_invariants_from_module()` helper function for loading from a module object

**sdd/scenario.py (extended)**
- Added `invariant_results` field to `ScenarioResult`
- Updated `ScenarioRunner.run()` to accept optional `invariants` parameter
- Updated `ScenarioRunner.run_all()` to accept optional `invariants` parameter
- Invariants are checked after scenario execution; failures mark the scenario as failed

**invariants/order_invariants.py**
- Reference invariants for OrderLifecycle machine
- Demonstrates the invariant function signature and patterns

### Invariant API

Invariants are functions with the signature:

```python
def invariant_name(log: EventLog, states: dict[tuple[str, str], str]) -> None:
    """Invariant docstring describing the property."""
    # Check the property
    # Raise AssertionError if violated
    assert condition, "Violation message"
```

The `states` dict maps `(machine_class_name, instance_id)` tuples to state names.

### Key Implementation Details

1. **Function-based invariants** — Invariants are plain Python functions, not classes. This keeps them simple and composable. Functions starting with `invariant_` are discovered automatically.

2. **AssertionError for violations** — Invariants raise `AssertionError` on violation. The error message becomes the violation details in `InvariantResult`.

3. **Dynamic loading** — `load_invariants(directory)` uses `importlib.util` to dynamically load Python files and extract invariant functions. Files starting with `__` are skipped.

4. **Scenario integration** — When `invariants` are passed to `ScenarioRunner.run()`, they're checked after all scenario steps complete. The states dict is built from `runner.machine_states()` with class names (not class objects) as keys.

5. **First failure in report** — If invariants fail, the first failure's details are used as the scenario's `failure_reason`.

### Test Coverage

30 tests in `tests/test_invariants.py` covering:
- InvariantResult and InvariantReport dataclasses
- check_invariant() and check_invariants() functions
- load_invariants() from directories (including subdirectories, error handling)
- load_invariants_from_module() helper
- Integration with OrderLifecycle machine
- ScenarioRunner with invariant checking (passing, failing, run_all)

### Files Added/Modified

```
sdd/
├── __init__.py         # Added invariants module exports
├── invariants.py       # NEW: Invariant checking module
└── scenario.py         # Added invariant support to ScenarioRunner

invariants/
└── order_invariants.py # NEW: Reference invariants for OrderLifecycle

tests/
└── test_invariants.py  # NEW: 30 tests
```

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v                    # All 255 tests
python -m pytest tests/test_invariants.py -v  # Just invariant tests (30)
```

### Example Usage

```python
from sdd.invariants import load_invariants, check_invariants
from sdd.scenario import ScenarioRunner, ScenarioParser

# Load invariants from directory
invariants = load_invariants("invariants/")

# Run scenario with invariant checking
runner = SimulationRunner()
runner.register(OrderLifecycle)

scenario_runner = ScenarioRunner(runner)
scenarios = ScenarioParser().parse_directory("scenarios/")

# Check invariants after each scenario
results = scenario_runner.run_all(scenarios, invariants=invariants)

for result in results:
    if not result.passed:
        print(f"Scenario '{result.scenario_name}' failed: {result.failure_reason}")
    if result.invariant_results and not result.invariant_results.all_passed:
        for failure in result.invariant_results.failures:
            print(f"  Invariant '{failure.invariant_name}': {failure.violation_details}")
```

### What's Next

The core SDD framework (Phases 1-6) is now complete:
1. Phase 1: Protocol (State, StateMachine, TransitionResult)
2. Phase 2: Reference Machine (OrderLifecycle)
3. Phase 3: SimulationRunner
4. Phase 4: Scenario Parser
5. Phase 5: Structural Validation
6. Phase 6: Invariant Checking

### Notes for Future Sessions

1. **States dict format** — The invariant `states` dict uses `(machine_name, instance_id)` string tuples, not class objects. This allows invariants to be defined without importing machine classes.

2. **Invariant naming convention** — Functions must start with `invariant_` to be discovered by `load_invariants()`.

3. **All 6 core phases complete** — The framework now has all core components for simulation-driven development: state machine protocol, event system, simulation runner, scenario parser, structural validation, and invariant checking.

---

## Session 7: Phase 7 Finish the Mechanical Layer (commit `447823f`)

### Motivation

After Sessions 1–6 produced a working framework, operating SDD on a multi-machine
stress test (a two-machine coffee-shop system) exposed a class of bugs the
framework couldn't catch mechanically — most notably **schema mismatches between
event emitters and subscriber resolvers**, which surfaced 2–5 steps downstream
as state-assertion failures rather than at the emit site.

This phase closes those gaps without changing the framework/runtime split. The
framework keeps everything that must be deterministic for correctness; Claude
Code keeps everything that benefits from judgment. The goal was to ensure the
mechanical layer fully delivers what the corrected framing claims it should.

### What Was Implemented

**sdd/runner.py (extended)**
- `RoutingFailure` dataclass — categorical record of event-routing problems.
- `_record_routing_failure()` helper, `routing_failures` property.
- Resolver-failure logging: `_subscribe_transition` handler now records
  `no_resolver_registered`, `resolver_returned_none`, or `resolver_raised`
  on the active StepResult instead of silently skipping.
- `guard_completeness_issues(machine_class)` — finds guarded transitions
  with no fallback branch (could fail `no_guard_passed` at runtime).
- `transition_coverage()`, `state_coverage()`, `event_path_coverage()` —
  feed Layer 2 coverage data into ConvergenceReport.
- `converge(scenarios, invariants)` — runs all three layers and returns a
  ConvergenceReport.
- Virtual clock: `virtual_clock` property, `advance_time(seconds/minutes/
  hours/days)`, `_next_due_timeout()` deadline scheduler, per-instance
  state-entry timestamp tracking.
- `_fire_internal` catches `SchemaValidationError` and records it as a
  `schema_validation_failed` StepError so a bad emit doesn't crash the cascade.

**sdd/events.py (extended)**
- `SchemaValidationError` exception.
- `validate_payload(payload, schema) -> list[str]` — minimal JSON-schema
  subset (required, properties.type) returning problem descriptions.
- `EventBus.register_schema(event_name, schema)` — conflict-checked registration.
- `EventBus.emit()` validates payload against any registered schema *before*
  logging or delivering the event. In strict mode (default), invalid
  emissions raise `SchemaValidationError`.

**sdd/protocol.py (extended)**
- `StateMachineMeta` discovers `@timeout`-decorated methods, resolves their
  transition names after `__set_name__`, and infers source state from the
  transition's first branch if not explicit.
- `_timeouts` ClassVar on StateMachine: list of `(source_state,
  transition_name, delay_seconds, method_name)`.

**sdd/convergence.py (new)** — `ConvergenceReport` aggregator
- Combines `StructuralReport`, `ScenarioResult[]`, coverage data, and
  `InvariantReport[]` into a single object.
- Derived properties: `structural_passed`, `scenarios_passed`,
  `invariants_passed`, `coverage_complete`, `is_converged`.
- `summary()` renders the polished output format shown in
  `docs/05-convergence-criteria.md`.

**sdd/adapters.py (new)** — Production-runner contract
- `Adapter` runtime-checkable Protocol: requires `attach(runner)`;
  `startup()`/`shutdown()` are optional.
- `ProductionRunner(SimulationRunner)`: manages adapter lifecycle, supports
  context-manager use, exposes `set_instance_loader(loader)` for persistence
  rehydration, overrides `get()` to consult the loader when an instance
  isn't in memory.

**sdd/timing.py (new)** — `@timeout` decorator
- `@timeout(transition, seconds=0, minutes=0, hours=0, days=0, from_state=None)`
  declares a time-based transition. The decorated method runs after the
  timeout-driven transition fires (analogous to `on_transition_*`).

**sdd/scenario.py (changed)**
- `_execute_advance_time_step` now calls `runner.advance_time(...)` instead
  of emitting a warning. AdvanceTimeStep in YAML now actually executes.

**machines/order_lifecycle.py (changed)**
- Removed redundant `guard_validate_to_cancelled` so the structural
  guard-completeness check passes. Demonstrates the canonical "last branch
  is the fallback" pattern.

### Resolution of Previously Deferred Items

| Deferred item from Sessions 4–6 | Status |
|---|---|
| Schema validation for event payloads | RESOLVED — `EVENT_SCHEMAS` + bus enforcement |
| Time advancement (`AdvanceTimeStep`) | RESOLVED — virtual clock + `@timeout` |
| Guard completeness checking | RESOLVED — `guard_completeness_issues()` |
| Convergence report generation | RESOLVED — `ConvergenceReport` + `runner.converge()` |
| Context assertions in scenarios | Still deferred |
| Scenario fragments (`use:` / `bind:`) | Still deferred |
| Scenario metadata parsing | Still deferred |

### Test Coverage

Tests: 255 → 311 (+56 in this session).

- `tests/test_runner.py` — 4 new RoutingFailure tests, 2 schema-integration tests
- `tests/test_structural.py` — 5 guard-completeness tests
- `tests/test_events.py` — 6 `validate_payload` tests, 7 schema-bus tests
- `tests/test_coverage.py` (new) — 5 coverage tests
- `tests/test_convergence.py` (new) — 6 ConvergenceReport tests
- `tests/test_adapters.py` (new) — 11 Adapter/ProductionRunner tests
- `tests/test_timing.py` (new) — 9 virtual-clock / `@timeout` tests

### Files Added/Modified

```
sdd/
├── __init__.py        (modified)  re-exports new symbols
├── adapters.py        (new)       Adapter protocol, ProductionRunner
├── convergence.py     (new)       ConvergenceReport
├── events.py          (modified)  SchemaValidationError, validate_payload, schema enforcement
├── protocol.py        (modified)  @timeout discovery in metaclass, _timeouts ClassVar
├── runner.py          (modified)  RoutingFailure, guard_completeness, coverage, converge, advance_time
├── scenario.py        (modified)  AdvanceTimeStep now executes
└── timing.py          (new)       @timeout decorator

machines/
└── order_lifecycle.py (modified)  removed redundant guard

tests/
├── test_adapters.py    (new)
├── test_convergence.py (new)
├── test_coverage.py    (new)
├── test_timing.py      (new)
├── test_events.py      (modified)  + schema tests
├── test_runner.py      (modified)  + RoutingFailure + schema integration tests
├── test_scenario.py    (modified)  advance_time-warning test → clock-advance test
└── test_structural.py  (modified)  + guard completeness tests
```

### Notes for Future Sessions

1. **The mechanical layer is now what the corrected framing claims it should
   be** — operations that must be deterministic for correctness (event routing
   diagnosability, schema agreement, coverage tracking, convergence aggregation)
   are in the framework. Operations that benefit from judgment (decomposition,
   diagnosis-and-fix, brief construction, adapter authorship for new infrastructure)
   stay with Claude Code as the framework's intended runtime.

2. **Schema registration is per-event globally** — if two machines try to
   register conflicting schemas for the same event name, `register_schema()`
   raises `ValueError`. Subscribers don't need to declare schemas themselves;
   the emitter's declaration is authoritative.

3. **Virtual clock is per-runner, not global** — `advance_time()` advances only
   that runner's clock. `reset()` zeroes it. Multiple runners in a test do
   not share virtual time.

4. **The structural `is_valid` is still strict** — dead letters flip it to
   False even when they're adapter-only events (notifications, audit). That's
   intentional: the framework enumerates candidates and lets the runtime
   (Claude Code) classify them. Future work could add an
   `EVENT_IS_ADAPTER_ONLY` declaration to suppress that signal for known cases.

---

## Session 8: Phase 8 End-to-End Reference Adapters (commit `6ff0f95`)

### Motivation

The original SDD claim — "machines built during simulation ship unchanged to
production" — was structurally supported by the framework but never demonstrated.
The `adapters/inbound/` and `adapters/outbound/` directories were empty. This
session builds the first end-to-end SDD project in the repo, proving the
convergence-to-production handoff works on the reference `OrderLifecycle`
machine without modifying a line of machine code.

### What Was Implemented

**adapters/outbound/sqlite_persistence.py** — `SqlitePersistenceAdapter`
- Outbound: subscribes to `*` on the bus, snapshots the source machine
  after every event, writes to a `machine_states` table keyed by
  `(machine_class, instance_id)`.
- Inbound: registers as the runner's `instance_loader`. When an event
  targets an instance not in memory, the loader hydrates from SQLite
  via `Machine.restore(snapshot)`.
- Stdlib only (`sqlite3`, `json`). ~95 lines.
- Uses `check_same_thread=False` because the HTTP adapter dispatches on
  a worker thread but bus delivery is synchronous and `HTTPServer` is
  single-threaded.

**adapters/inbound/http_api.py** — `HttpInboundAdapter`
- Stdlib `http.server` on a background daemon thread.
- Routes `POST /machines/{ClassName}/{instance_id}/{transition}` to
  `runner.fire(...)` with the JSON body as kwargs.
- Returns 200 with serialized `StepResult` on success, 400 when
  `result.errors` or `result.routing_failures` are non-empty, 404 for
  unknown machine class or path mismatch.
- No external dependencies. ~115 lines.

**tests/test_integration_e2e.py** — 5 integration tests
- `TestHttpAdapterBasics` (2 tests): unknown machine → 404; unknown
  transition → 400 with the underlying `step_error.error_type`
  surfaced in the response body.
- `TestE2EOrderFlow.test_happy_path_through_http`: drive an order
  through validate → fulfill → complete over HTTP, confirm final
  state is "completed".
- `TestProcessBoundarySurvival.test_restart_preserves_machine_state`
  — the headline test. Drive partway, tear down the runner entirely,
  spin up a fresh runner against the same SQLite file with zero
  in-memory instances, fire the next transition. Rehydration via
  `snapshot()`/`restore()` completes the scenario; final state +
  context survive the restart.
- `TestSimulationVsProduction.test_final_state_matches_simulation` —
  the SDD invariant: identical final state whether the scenario runs
  through `SimulationRunner` alone or through `ProductionRunner` with
  both adapters attached.

### Test Coverage

Tests: 311 → 316 (+5 integration tests). All passing.

### Files Added

```
adapters/
├── __init__.py
├── inbound/
│   ├── __init__.py
│   └── http_api.py             HttpInboundAdapter
└── outbound/
    ├── __init__.py
    └── sqlite_persistence.py   SqlitePersistenceAdapter

tests/
└── test_integration_e2e.py     5 e2e tests
```

`machines/order_lifecycle.py` was **not modified** between Phase 7 and Phase 8.
That is the SDD claim made concrete: the machine that converges in simulation
is the same Python source that runs in production.

### Notes for Future Sessions

1. **The adapter contract is now exercised** — `Adapter` protocol +
   `ProductionRunner` + `set_instance_loader` were defined in Phase 7 but
   first used in Phase 8. Both adapters follow the documented rule:
   no domain logic, only mechanical translation.

2. **The HTTP adapter's implicit-instance-creation** (creating a new
   instance from kwargs when the runner has none) is correct for greenfield
   instances but means the *first* call to a fresh instance ID will create
   a new placed-state machine. The persistence loader runs *before* this
   path (via `ProductionRunner.get`), so existing instances are rehydrated
   correctly. New instances start fresh as expected.

3. **SQLite thread-safety** — production deployments using a multi-threaded
   HTTP server would need either per-thread connections, a connection
   pool, or `WAL` mode. For the reference case (single-threaded HTTPServer),
   `check_same_thread=False` is the simplest correct option.

4. **The `event.timestamp` field is currently `time.monotonic()`**, which
   resets between processes. The persistence layer uses it as
   `updated_at` but doesn't depend on it for correctness. If global
   timeline ordering across processes is needed, switch to a real clock
   or a logical-clock counter.

5. **Adapters do not show up in coverage tracking** — `transition_coverage()`
   inspects scenario step results. HTTP-driven traffic during integration
   tests isn't currently fed back into coverage. If end-to-end coverage
   becomes desired, the HTTP adapter could push `StepResult` records into
   a list the runner exposes.
