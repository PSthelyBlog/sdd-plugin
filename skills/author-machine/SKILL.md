---
name: sdd-author-machine
description: Author a single state machine file from a decomposition brief — Phase 1 of the SDD workflow. Generates a fully-formed machines/{name}.py satisfying the SDD protocol (states, transitions, guards, side effects, EVENT_SCHEMAS, subscriptions). Optionally dispatches subagents to author multiple machines in parallel. Use when the decomposition is approved and a specific machine needs to be implemented. Triggers include "implement MachineName", "author the X machine", "sdd:author-machine", "write the OrderLifecycle machine".
---

# sdd:author-machine — Author a state machine

You are doing Phase 1 of SDD: turning an approved decomposition brief into a working machine file. The output is a single Python module at `machines/{snake_case_name}.py` that satisfies the SDD protocol exactly.

## When this skill fires

- A decomposition exists (from `sdd:decompose`) and has been approved
- The user wants a specific machine implemented, or wants all undeclared machines implemented in parallel

## Reference: the protocol

Every machine satisfies this contract (see `docs/02-state-machine-protocol.md` in the framework repo):

- Subclass `StateMachine` from `sdd.protocol`
- Exactly one `State(initial=True)` and at least one `State(final=True)`
- Transitions: `target = source.to(other)`, combined with `|` for branches and multi-source
- Guards: methods named `guard_{transition}_to_{target}` returning `bool` — pure functions of context + kwargs
- Side effects: `on_enter_{state}` / `on_exit_{state}` / `on_transition_{transition}` — may emit events, may not do I/O
- Event emission: `self.emit("domain.event", payload_dict)` — never raise, never call external services
- `EVENT_SCHEMAS` class attribute: dict mapping each emitted event name to a schema `{"required": [...], "properties": {...}}`
- `subscriptions()` classmethod: dict mapping incoming event names to transition names
- No imports outside `sdd.protocol` and the stdlib

## How to author one machine

### 1. Receive the brief

The brief should contain: machine name, purpose, states (with initial/final markers), transitions, events emitted (with payload schemas), events consumed, context fields, and guard hints. If anything is missing, ask the user before writing code.

### 2. Generate the file

Use this exact skeleton, adapted to the brief:

```python
"""{MachineName}: {one-line purpose from brief}."""

from sdd.protocol import State, StateMachine


class {MachineName}(StateMachine):
    """{full purpose paragraph}

    States:
        {state_a} (initial) → {state_b} → {state_c} (final)
                                       ↘ {state_d} (final)

    Context fields:
        field1: type - description
        field2: type - description
    """

    EVENT_SCHEMAS = {
        "domain.event_one": {
            "required": ["field1", "field2"],
            "properties": {
                "field1": {"type": "string"},
                "field2": {"type": "array"},
            },
        },
        # ... one entry per emitted event
    }

    # States
    state_a = State(initial=True)
    state_b = State()
    state_c = State(final=True)
    state_d = State(final=True)

    # Transitions
    transition_x = state_a.to(state_b)
    transition_y = state_b.to(state_c) | state_b.to(state_d)
    # use (state_a | state_b).to(target) for multi-source

    # Guards
    def guard_transition_y_to_state_c(self, **kwargs) -> bool:
        """{one-line business rule from brief}"""
        return bool(self._context.get("some_field"))
    # guard_transition_y_to_state_d omitted — state_d is the fallback branch

    # Side effects — emit events on state entry
    def on_enter_state_b(self) -> None:
        self.emit("domain.event_one", {
            "field1": self._context.get("field1"),
            "field2": self._context.get("field2"),
        })

    def on_enter_state_c(self) -> None:
        self.emit("domain.event_two", {
            "field1": self._context.get("field1"),
        })

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        """Map incoming event names to transition names."""
        return {
            "other_domain.something_happened": "transition_x",
        }
```

### 3. Key rules to enforce while writing

- **Guards must be pure.** Read context and kwargs. Do not mutate context. Do not call I/O.
- **The last branch of a guarded transition has no explicit guard.** Per the documented "fallback" pattern. If you write a guard for every branch, the structural check will flag it.
- **Events are past-tense facts.** `order.validated`, not `validate_order`.
- **EVENT_SCHEMAS must list every event the machine emits.** Look at every `self.emit(...)` call and ensure the schema exists. The bus will reject emits with payloads that don't match.
- **Subscriptions only declare transitions the machine actually has.** A typo here becomes a phantom subscription caught by structural check, but better to get it right.
- **No infrastructure imports.** `import requests`, `import sqlalchemy`, `import redis` — anything beyond `sdd.protocol` and stdlib — is wrong in a machine file.

### 4. Verify the machine in isolation

After writing, run:

```python
from sdd.runner import SimulationRunner
from machines.{module} import {MachineName}

runner = SimulationRunner()
runner.register({MachineName})
report = runner.check()
print(f"Issues for {MachineName}: ", report.unreachable_states, report.terminal_states, report.guard_completeness_issues)
```

If the report flags anything, fix before moving on.

### 5. Suggest the next step

When one machine is done and verified:

> {MachineName} written and structurally clean. {Next undone machine} is next, or run `/sdd:converge` once all machines exist to wire them together and check cross-machine integration.

## Dispatching subagents for parallel authoring

If the user wants multiple machines authored in parallel:

1. Use the `Agent` tool with `subagent_type="general-purpose"` for each machine.
2. Each subagent gets a brief containing **only that machine's** specification, plus a reference to this skill's protocol contract.
3. Each subagent writes one file and returns the path.
4. After all subagents complete, you (the orchestrator) run structural checks on each machine individually, then run `sdd:converge` to assemble them.

Sample subagent prompt:

```
You are implementing one state machine for an SDD project. The machine is
`{MachineName}`. Write it to `machines/{snake_case}.py` following the SDD
protocol exactly. Do not write any other files.

Brief:
- Purpose: ...
- States: ...
- Transitions: ...
- Events emitted (with schemas): ...
- Events consumed: ...
- Context fields: ...
- Guard hints: ...

Protocol rules (non-negotiable):
- Subclass `sdd.protocol.StateMachine`
- Exactly one `State(initial=True)`, at least one `State(final=True)`
- Guards are pure (no I/O, no context mutation)
- Side effects call `self.emit(...)` only — no direct I/O
- EVENT_SCHEMAS lists every emitted event with required fields
- The last branch of a guarded transition has no explicit guard (fallback)
- No imports outside `sdd.protocol` and stdlib
```

## What not to do

- Do not add helper functions or utilities in the machine file. One class, plus its EVENT_SCHEMAS dict.
- Do not write tests in this skill. The core is tested via simulation (Phase 2-5), not unit tests.
- Do not write the adapter for this machine. That's `sdd:adapt`, only after convergence.
- Do not skip `EVENT_SCHEMAS`. The bug-2 stress test in the framework's history shows what happens when schemas are missing — bugs surface 5 steps downstream.
- Do not put error handling (try/except) inside the machine. Failures are states.
