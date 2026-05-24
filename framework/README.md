# Simulation-Driven Development (SDD)

A development methodology where Claude Code decomposes tasks into Python state machines, simulates their interactions to verify correctness, and delivers production-ready domain logic with I/O adapters bolted on afterward.

## Core Premise

The state machines built during simulation **are** the production system's core. Nothing is thrown away. Nothing is rewritten. The simulation phase produces domain logic; the adaptation phase connects it to the real world.

## How It Works

```
User ──(task)──▶ Claude Code ──(decompose)──▶ Subagents
                      ▲                            │
                      │                    Python State Machines
                      │                            │
                      └───(operate/observe)─────────┘
```

1. **User** sends a development task to Claude Code
2. **Claude Code** decomposes the task into interacting domains, each modeled as a state machine
3. **Subagents** implement the state machines following the protocol specification
4. **Claude Code** operates the state machines through scenarios, observes behavior, and iterates
5. **Convergence** is reached when structural, scenario, and invariant checks all pass
6. **Subagents** write thin I/O adapters to connect the machines to infrastructure
7. The state machines go to production unchanged

## Documentation

| Document | Purpose |
|---|---|
| [Introduction](docs/00-introduction.md) | What SDD is, why it works, and how to read the rest of the docs — start here |
| [Architecture](docs/01-architecture.md) | System structure, layers, and data flow |
| [State Machine Protocol](docs/02-state-machine-protocol.md) | Base class contract all machines must follow |
| [Event System](docs/03-event-system.md) | Inter-machine communication and event log |
| [Simulation Runner](docs/04-simulation-runner.md) | How Claude Code operates and observes machines |
| [Convergence Criteria](docs/05-convergence-criteria.md) | When the system is considered correct |
| [I/O Adapter Specification](docs/06-io-adapters.md) | Connecting production-core machines to infrastructure |
| [Workflow Guide](docs/07-workflow-guide.md) | Step-by-step process for Claude Code and subagents |
| [Scenario Language](docs/08-scenario-language.md) | How scenarios are defined, decomposed, and replayed |

## Principles

**State machines are the source of truth.** Domain logic lives in states, transitions, guards, and events. Not in controllers, services, or handlers.

**Simulation is not testing.** Testing verifies code after writing it. Simulation drives the writing. Claude Code runs machines, discovers what's missing, and directs subagents to fix it.

**Failures are domain logic.** Error states and timeout transitions are modeled explicitly in the machines, not handled by infrastructure.

**I/O is peripheral.** Databases, HTTP, queues — these are adapters around a pure core. They are thin, mechanical, and boring by design.

**Convergence is layered.** Structural completeness first, then scenario coverage, then cross-machine invariants. Each layer must pass before the next is meaningful.
