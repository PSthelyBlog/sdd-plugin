# Workflow Guide

## Overview

This document describes the step-by-step process Claude Code follows from receiving a user task through delivering a converged, production-ready system. Each phase has clear entry criteria, activities, and exit criteria.

## Phase 0: Task Reception

**Trigger:** The user sends a development task to Claude Code.

**Claude Code's first action:** Understand the task well enough to identify domains, entities, and processes. Claude Code does not write any code yet. It produces a **task decomposition** in plain language.

Example task: *"Build a subscription billing system that handles plan changes, payment retries, and cancellations."*

Decomposition:

```
Domains identified:
  1. SubscriptionLifecycle — manages the subscription from creation to cancellation
  2. BillingCycle — manages recurring charge attempts per billing period
  3. PaymentAttempt — manages individual payment attempts with retry logic
  4. PlanManagement — manages plan changes (upgrades, downgrades)

Key interactions:
  - BillingCycle triggers PaymentAttempt at the start of each period
  - PaymentAttempt failure triggers retry logic within BillingCycle
  - Repeated failures cause BillingCycle to escalate to SubscriptionLifecycle
  - PlanManagement changes affect the next BillingCycle's amount
  - Cancellation in SubscriptionLifecycle halts future BillingCycles

Implied failure paths:
  - Payment fails → retry → retry → escalate → suspend subscription
  - Plan change during active billing period → prorate or defer
  - Cancellation with remaining balance → handle final charge
```

**Exit criteria:** Claude Code has identified all machines, their primary states, their interactions, and the key failure paths. The user can review and adjust before any implementation begins.

## Phase 1: Subagent Dispatch — Machine Implementation

**Entry criteria:** Task decomposition approved (or not contested).

**Claude Code's action:** Dispatch one subagent per machine with a specific brief.

### Subagent Brief Structure

Each subagent receives:

1. **Machine name and purpose** — one sentence describing what this machine tracks.
2. **States** — the expected states, with initial and final markers.
3. **Transitions** — the expected transitions, including failure branches.
4. **Events to emit** — what events this machine should produce, with payload schemas.
5. **Events to consume** — what events from other machines this one reacts to.
6. **Context fields** — what data the machine carries.
7. **Guard hints** — business rules that should gate transitions.

Example brief for `PaymentAttempt`:

```
Machine: PaymentAttempt
Purpose: Manages a single attempt to charge a customer.

States:
  - pending (initial)
  - processing
  - succeeded (final)
  - failed (final)

Transitions:
  - process: pending → processing
  - succeed: processing → succeeded
  - fail: processing → failed

Events to emit:
  - payment.succeeded {subscription_id, amount, transaction_id}
  - payment.failed {subscription_id, amount, failure_reason, attempt_number}

Events to consume:
  - billing.charge_initiated → triggers "process" transition

Context fields:
  - subscription_id, amount, attempt_number, failure_reason, transaction_id

Guards:
  - process guard: amount must be positive
```

### Subagent Deliverables

Each subagent returns a Python module containing:

- The machine class following the protocol specification
- Event payload schemas for all events emitted
- Docstrings on every guard explaining the business rule

Subagents work independently. They do not see each other's code. Integration is Claude Code's responsibility.

**Exit criteria:** All machine modules received, each individually passing structural checks (correct protocol usage, exactly one initial state, at least one final state, etc.).

## Phase 2: Assembly

**Entry criteria:** All machine modules received and individually valid.

Claude Code assembles the machines into the simulation runner:

1. Register all machine classes.
2. Wire up resolvers.
3. Declare event schemas from all machines.
4. Run cross-machine structural checks.

This is where integration issues surface. Common problems at this stage:

- **Schema mismatches.** Machine A emits an event with field `order_id` but Machine B's subscription handler expects `subscription_id`. Claude Code identifies the mismatch and sends a targeted fix request to the responsible subagent.

- **Dead letters.** Machine A emits an event nobody listens to. Claude Code determines whether a subscription is missing or the event shouldn't be emitted.

- **Phantom subscriptions.** Machine B subscribes to an event nobody emits. Claude Code determines whether an emission is missing or the subscription is wrong.

**Exit criteria:** All structural checks pass (Layer 1 convergence).

## Phase 3: Scenario Development

**Entry criteria:** Layer 1 convergence achieved.

Claude Code develops scenarios from three sources:

### Happy Path Scenarios
Derived from the user's task description. These represent the intended normal operation of the system. For the subscription billing example:
- New subscription → first billing → payment succeeds → subscription active
- Plan upgrade → next billing at new rate → payment succeeds

### Failure Path Scenarios
One scenario per failure branch per guarded transition, generated systematically:
- Payment fails on first attempt
- Payment fails on all retry attempts → subscription suspended
- Plan change rejected (e.g., downgrade during grace period)
- Cancellation during pending payment

### Timeout Scenarios
One scenario per time-based transition:
- Payment retry after 24-hour delay
- Subscription suspension after 3 failed billing cycles
- Grace period expiry after cancellation

Claude Code writes each scenario as a sequence of steps with expected outcomes, following the format in the Simulation Runner specification.

**Exit criteria:** Scenario set covers all transitions, all states, all guard branches, and all event subscriptions.

## Phase 4: Simulation Loop

**Entry criteria:** Scenario set developed.

This is the core iterative phase. Claude Code runs scenarios through the runner and acts on the results.

```
Run scenario ──▶ Examine results ──▶ All pass? ──Yes──▶ Next scenario
                       │                                      │
                       No                                All done?
                       │                                      │
                       ▼                               Yes    No
                 Diagnose failure                       │      │
                       │                               ▼      ▼
                       ▼                          Phase 5   Loop back
                 Fix or delegate
                       │
                       ▼
                 Rerun scenario
```

### Diagnosis Process

When a scenario fails, Claude Code follows this diagnostic sequence:

1. **Read the StepResult.** Which step failed? What was the error type?

2. **Transition failure (`no_guard_passed`):** The machine rejected the input. Is the guard too strict (bug) or is the scenario feeding invalid data (bad scenario)? Claude Code examines the guard logic and the step's arguments to decide.

3. **Unexpected state:** A machine reached a state the scenario didn't expect. Claude Code traces the event log to find which event cascade led to the unexpected state. Was it a missing guard? An incorrect subscription? An event emitted with wrong data?

4. **Missing event:** An expected event was never emitted. Claude Code checks whether the transition that should have emitted it actually fired. If it fired but didn't emit, the `on_enter_*` side effect is wrong. If it didn't fire, trace back to find why.

5. **Cascade issue:** Events propagated in an unexpected order or too deeply. Claude Code examines the event log for circular patterns or unexpected subscription triggers.

### Fix Process

Claude Code either fixes the issue directly (for simple corrections like a typo in a guard condition) or sends a targeted fix request to the responsible subagent. Fix requests include:

- The specific machine and location of the problem
- The scenario that triggered it
- The expected behavior
- The actual behavior
- The event log excerpt showing the failure

After the fix, Claude Code reruns the failing scenario and all previously passing scenarios (regression check).

**Exit criteria:** All scenarios pass. Transition, state, branch, and event path coverage are 100%.

## Phase 5: Invariant Verification

**Entry criteria:** Layer 2 convergence achieved.

Claude Code defines and checks property invariants.

### Invariant Proposal

Claude Code proposes invariants based on domain understanding:

- Ordering constraints: "X must happen before Y"
- Exclusivity constraints: "X and Y cannot both be true"
- Conservation constraints: "every A must be matched by a B"
- Idempotency constraints: "X can happen at most once"

These are presented as Python assertion functions (see Convergence Criteria specification). Claude Code writes them, then runs all scenarios with invariant checking enabled.

### Invariant-Driven Discovery

Invariant failures often reveal edge cases that scenarios missed. When an invariant fails, Claude Code:

1. Creates a new scenario isolating the violating sequence.
2. Fixes the underlying machine logic.
3. Adds the new scenario to the permanent scenario set.

This is why invariant checking comes last — it serves as a safety net catching cross-machine issues that per-machine scenario testing might miss.

**Exit criteria:** All invariants pass across all scenarios. Convergence report generated.

## Phase 6: I/O Adaptation

**Entry criteria:** Full convergence (all three layers).

Claude Code inventories the I/O requirements:

```
Inbound:
  - HTTP API for subscription creation, plan changes, cancellation
  - Webhook receiver for payment processor callbacks
  - Cron trigger for billing cycle initiation

Outbound:
  - Database persistence for all machine states
  - Payment processor API calls
  - Email notifications for billing events
  - Webhook emissions for partner integrations
```

Claude Code dispatches subagents to write adapters. Each adapter brief includes:

- Which events or transitions it handles
- The external system's interface (API schema, database schema)
- Error handling requirements (retry policy, circuit breaker needs)
- The adapter design rules from the I/O Adapter specification

Adapters are tested independently against mocked infrastructure.

**Exit criteria:** All adapters written, individually tested, and attached to the production runner. Integration test with containerized infrastructure passes the same scenarios as simulation.

## Phase 7: Delivery

**Entry criteria:** Integration tests pass.

Claude Code delivers to the user:

1. **Convergence report** — proof that the domain logic is correct.
2. **Machine source code** — the production core.
3. **Adapter source code** — the infrastructure layer.
4. **Scenario set** — reusable as regression tests.
5. **Invariant set** — reusable as property-based checks.
6. **Simulation runner configuration** — for ongoing development and debugging.

The simulation infrastructure remains active. When the user requests changes, the process begins again at Phase 0, with existing machines and scenarios as the starting point.
