# CLAUDE.md

## Project

This is the Simulation-Driven Development (SDD) framework. State machines are the production core — they are built and verified through simulation, then connected to infrastructure via thin I/O adapters. Read the full documentation in `docs/` before making architectural decisions.

## Repository Structure

```
simulation-driven-development/
├── CLAUDE.md
├── README.md
├── docs/
│   ├── 01-architecture.md          # System layers and data flow
│   ├── 02-state-machine-protocol.md # Base class contract
│   ├── 03-event-system.md          # Inter-machine communication
│   ├── 04-simulation-runner.md     # Operating and observing machines
│   ├── 05-convergence-criteria.md  # When the system is correct
│   ├── 06-io-adapters.md           # Connecting to infrastructure
│   ├── 07-workflow-guide.md        # Step-by-step process
│   └── 08-scenario-language.md     # Defining and running scenarios
├── sdd/                            # Framework source code
│   ├── protocol.py                 # StateMachine base class, State, TransitionResult
│   ├── events.py                   # Event, EventBus, event log
│   ├── runner.py                   # SimulationRunner
│   └── scenario.py                 # Scenario parser and step execution
├── machines/                       # Domain state machines (production core)
├── adapters/                       # I/O adapters (inbound, outbound, persistence)
│   ├── inbound/
│   └── outbound/
├── scenarios/                      # Scenario definitions (YAML)
├── invariants/                     # Property invariant functions
└── tests/                          # Adapter tests only — core is tested via simulation
```

## Core Principles

These are non-negotiable. Every decision must be consistent with them.

1. **State machines are the production core.** They are not prototypes, not models, not scaffolding. The code built during simulation ships unchanged.

2. **Zero infrastructure imports in machines.** A machine module imports only `sdd.protocol` and the Python standard library. No database drivers, no HTTP libraries, no framework code. Ever.

3. **Events are the only inter-machine coupling.** No machine holds a reference to another machine. No machine calls another machine's methods. Communication is exclusively through `self.emit()` and the event bus.

4. **Failures are explicit states.** Error handling is modeled as states and transitions, not try/except blocks. If something can fail, there is a state for it and a transition to it.

5. **Adapters contain no domain logic.** If an adapter has an `if` statement evaluating a business rule, that logic belongs in a machine guard.

6. **Convergence before adaptation.** No adapter code is written until all three convergence layers pass: structural completeness, scenario coverage, property invariants.

## Working with State Machines

### Creating a Machine

Follow the protocol in `docs/02-state-machine-protocol.md`. Every machine must have:
- Exactly one `State(initial=True)`
- At least one `State(final=True)`
- Guards named `guard_{transition}_to_{target}` that are pure (no I/O, no context mutation)
- Side effects (`on_enter_*`, `on_exit_*`) that communicate only via `self.emit()`
- A `subscriptions()` classmethod declaring which external events trigger which transitions
- Serialization support via `snapshot()` and `restore()`

### Guard Rules

Guards return `bool`. They read `self.context` and `**kwargs`. They must not:
- Mutate `self.context`
- Perform any I/O
- Access any external state
- Call `self.emit()`

### Event Naming

Events use `{domain}.{past_tense_action}` format: `order.validated`, `payment.failed`, `inventory.released`. Events are facts about what happened, not commands.

### Context Updates

Keyword arguments passed to `machine.fire("transition", **kwargs)` merge into `self.context`. This is the only mechanism for feeding new data into a machine.

## Working with the Simulation Runner

The runner is operated programmatically — there is no CLI. You import the
framework into a driver script (or invoke it inline via `python -c`) and
call its methods directly. This matches the design assumption: the runtime
is Claude Code, not a human at a terminal.

### Structural analysis only (no scenarios)

```python
from sdd.runner import SimulationRunner
from machines.order_lifecycle import OrderLifecycle

runner = SimulationRunner()
runner.register(OrderLifecycle)
report = runner.check()  # StructuralReport
print(report.is_valid, report.dead_letters, report.guard_completeness_issues)
```

### Scenario execution

```python
from sdd.scenario import ScenarioParser, ScenarioRunner

scenarios = ScenarioParser().parse_directory("scenarios")
sr = ScenarioRunner(runner)
results = sr.run_all(scenarios)  # list[ScenarioResult]
```

### Full convergence (structural + scenarios + invariants + coverage)

```python
from sdd.invariants import load_invariants

report = runner.converge(
    scenarios=ScenarioParser().parse_directory("scenarios"),
    invariants=load_invariants("invariants"),
)  # ConvergenceReport
print(report.summary())     # polished output, docs/05 format
print(report.is_converged)  # True only when all three layers pass + coverage complete
```

### Convergence Checks

Always run convergence in order. Do not skip layers.

1. **Layer 1: structural** — `runner.check()` → reachability, deadlocks, dead letters, phantom subscriptions, guard completeness
2. **Layer 2: scenarios** — `ScenarioRunner.run_all(scenarios)` → transition / state / branch / event-path coverage
3. **Layer 3: invariants** — pass `invariants=` to `runner.converge(...)` or to `ScenarioRunner.run_all(...)` → cross-machine property checks

If structural checks fail, fix them before running scenarios. If scenarios fail, fix them before checking invariants. `runner.converge()` runs all three and aggregates into a single `ConvergenceReport`.

## Subagent Workflow

When dispatching subagents to implement machines:

1. **Give each subagent a brief** containing: machine name, purpose, states, transitions, events to emit (with payload schemas), events to consume, context fields, and guard hints. See `docs/07-workflow-guide.md` Phase 1 for the brief format.

2. **Subagents work independently.** They do not see each other's code. Integration is your responsibility.

3. **Validate deliverables immediately.** Run structural checks on each returned machine before assembling. Catch protocol violations early.

4. **Assembly is where integration issues surface.** Schema mismatches, dead letters, and phantom subscriptions appear when machines are registered together. Send targeted fix requests — include the failing machine, the scenario, expected vs. actual behavior, and the relevant event log excerpt.

## Diagnosis Sequence

When a scenario fails, follow this order:

1. Read the `StepResult` — which step failed and what was the error type?
2. `no_guard_passed` → Is the guard too strict, or is the scenario feeding bad data?
3. Unexpected state → Trace the event log to find the cascade that led there.
4. Missing event → Did the transition that should emit it actually fire?
5. Cascade issue → Check the event log for circular patterns.

Fix, then rerun the failing scenario AND all previously passing scenarios (regression).

## File Conventions

- Machine files: `machines/{domain_name}.py` — one machine per file, class name is PascalCase
- Event schemas: defined in each machine file alongside the machine class
- Scenarios: `scenarios/{category}/{descriptive_name}.yaml`
- Invariants: `invariants/{domain_or_cross_cutting}.py` — one function per invariant
- Adapter files: `adapters/inbound/{interface}.py`, `adapters/outbound/{system}.py`

## What Not to Do

- Do not put domain logic in adapters. If you're writing a business rule outside `machines/`, stop.
- Do not create machines without final states. Every process terminates.
- Do not use direct machine-to-machine references. Use events.
- Do not write adapter code before convergence. The core must be proven correct first.
- Do not skip scenario replay after fixes. A change to one machine can break another through event cascading.
- Do not add infrastructure dependencies to machine files. Check imports — if you see `import requests`, `import sqlalchemy`, `import redis`, or anything similar in a machine file, it's wrong.
