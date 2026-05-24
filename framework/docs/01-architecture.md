# Architecture

## Overview

SDD produces systems with three distinct layers. The inner layer is built during simulation and never changes. The outer layers are added afterward to connect the system to the real world.

```
┌─────────────────────────────────────────────────┐
│                 Inbound Adapters                │
│          (HTTP, CLI, queue consumers)           │
├─────────────────────────────────────────────────┤
│                                                 │
│              State Machine Core                 │
│                                                 │
│   ┌───────────┐  events  ┌───────────┐         │
│   │ Machine A ├─────────▶│ Machine B │         │
│   └───────────┘          └─────┬─────┘         │
│                                │ events         │
│                          ┌─────▼─────┐         │
│                          │ Machine C │         │
│                          └───────────┘         │
│                                                 │
├─────────────────────────────────────────────────┤
│                Outbound Adapters                │
│        (databases, APIs, email, queues)         │
└─────────────────────────────────────────────────┘
```

## Layer Definitions

### State Machine Core

This is the production core. It contains all domain logic expressed as state machines communicating through an event bus. This layer has **zero infrastructure dependencies** — no imports of database drivers, HTTP libraries, or framework code. It depends only on the SDD base protocol and Python's standard library.

The core is built and verified entirely during the simulation phase. Claude Code operates it directly, feeding synthetic inputs and observing transitions and events. Everything discovered during simulation — missing states, unhandled transitions, failure paths — is fixed here before any adapter is written.

### Inbound Adapters

Inbound adapters translate external signals into state machine transitions. An HTTP endpoint receives a request, extracts the relevant data, locates the appropriate machine instance, and calls a transition. A queue consumer reads a message and does the same.

Inbound adapters contain no domain logic. They perform three tasks: deserialize input, locate a machine instance, and invoke a transition. If an adapter contains an `if` statement about business rules, that logic belongs in a machine guard instead.

### Outbound Adapters

Outbound adapters subscribe to machine events and side-effect state entries, then perform infrastructure operations. When a machine enters a state or emits an event, an outbound adapter might persist the state to a database, send an email, or publish to a message queue.

Outbound adapters are invisible to the state machines. A machine emits an event; it does not know or care what listens. This means the simulation environment can run the full core without any outbound adapters present.

### Persistence Adapter (Special Case)

The persistence adapter deserves separate mention because it spans both directions. It handles serializing machine state to durable storage on state changes (outbound) and rehydrating machine instances from storage on startup or when a new transition arrives for an existing process (inbound).

The core machines must support serialization and deserialization of their state and context through the protocol's `snapshot` and `restore` methods. These methods are pure data transformations, not I/O operations. The persistence adapter calls them and handles the actual storage.

## Deployment Topology

The architecture is deliberately agnostic about deployment. The same state machine core can run in:

- A single process (all machines in memory, event bus is synchronous)
- Multiple processes (machines partitioned by type, event bus backed by a message queue)
- Serverless functions (each machine instance rehydrated from storage per invocation)

The I/O adapters change; the core does not. This is the central architectural guarantee.

## Directory Structure

```
project/
├── machines/              # State Machine Core
│   ├── __init__.py
│   ├── order_lifecycle.py
│   ├── payment_flow.py
│   └── inventory_state.py
├── events/                # Event definitions and bus
│   ├── __init__.py
│   ├── bus.py
│   └── schema.py
├── adapters/              # I/O Adapters
│   ├── inbound/
│   │   ├── http_api.py
│   │   └── queue_consumer.py
│   └── outbound/
│       ├── persistence.py
│       ├── notifications.py
│       └── external_apis.py
├── simulation/            # Simulation infrastructure
│   ├── runner.py
│   ├── scenarios/
│   └── invariants/
└── tests/                 # Adapter tests (core tested via simulation)
    ├── test_inbound/
    └── test_outbound/
```

Note that `machines/` and `events/` have no dependency on `adapters/`. The dependency arrow points inward only.
