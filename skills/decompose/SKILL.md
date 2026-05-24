---
name: sdd-decompose
description: Decompose a task description into interacting state machines — Phase 0 of the SDD workflow. Produces a plain-language breakdown of domains, states, transitions, events, and failure paths for the user to review before any code is written. Use when the user has stated a problem ("build an order system", "handle subscription billing", "track loan applications") and the SDD layout is already in place. Triggers include "decompose this", "break this into machines", "sdd:decompose", "plan the SDD machines for X".
---

# sdd:decompose — Decompose a problem into state machines

You are doing Phase 0 of the SDD workflow: turning a user's task description into a structured decomposition. This is the only phase that produces prose, not code. The user reviews and approves the decomposition before machine implementation begins.

## When this skill fires

- The user has described a problem ("build an order processing system", "handle subscription billing with retries")
- The SDD project layout is in place (run `sdd:start` first if not)
- No machines have been written yet — or the user wants to add new machines to an existing project

## What to produce

A markdown document with this exact structure:

```markdown
# Decomposition: <project name>

## Task summary
<one or two sentences restating the user's problem>

## Domains identified
1. **MachineName** — one-sentence purpose
2. **MachineName** — one-sentence purpose
3. ...

## Per-machine breakdown

### MachineName

**Purpose:** what this machine tracks

**States:**
- `state_a` (initial)
- `state_b`
- `state_c` (final)
- `state_d` (final)

**Transitions:**
- `transition_x`: state_a → state_b
- `transition_y`: state_b → state_c | state_d  (guarded)
- ...

**Events emitted:**
- `domain.event_one` {field1, field2}
- `domain.event_two` {field1}

**Events consumed (subscriptions):**
- `other_domain.event_x` → triggers `transition_x`

**Context fields:**
- field1, field2, field3

**Guards (when guarded transitions branch):**
- guard_y_to_c: when [condition]
- guard_y_to_d: fallback

## Key interactions
- MachineA emits `a.done` → MachineB.start fires
- MachineB emits `b.failed` after 3 attempts → MachineA.cancel fires
- ...

## Failure paths to model
- Customer payment fails after retries → order cancelled, inventory released
- Machine times out → escalation
- ...

## Cross-machine invariants to verify
- An order cannot be `completed` unless payment was `captured`
- Inventory reserved must be released when order is cancelled
- No double-charging: payment.captured fires at most once per order
- ...
```

## How to derive each section

### Domains identified

Extract nouns from the task description. Each major **process** or **lifecycle** that has its own state is a machine. Don't conflate: an order has a lifecycle; a payment has a lifecycle; even if they're tightly coupled, they're separate machines.

Rules of thumb:
- "Things that get reserved, allocated, expired" → its own machine
- "Things that retry independently" → its own machine
- "Things with their own timeout" → its own machine
- One machine per entity-with-a-lifecycle, not per data record

### States and transitions

Walk through the lifecycle as a user story. Each waypoint is a state. Each user action or system event that moves the entity forward is a transition. Mark exactly one `initial=True` and at least one `final=True` per machine.

Failure states are explicit states, never try/except. If something can fail, it has a state and a transition to it.

### Events

Events follow `{domain}.{past_tense_action}` — `order.validated`, not `validate_order`. The emitter declares the payload schema (this becomes `EVENT_SCHEMAS` later). Be specific about which fields are required.

### Subscriptions

If MachineB needs to react to MachineA's event, declare it as a subscription on MachineB. Never call MachineA's methods from MachineB.

### Invariants

These are cross-machine rules — what should be true across the whole system, not within one machine. Common patterns:
- **Ordering:** "X must happen before Y"
- **Exclusivity:** "X and Y can't both be true"
- **Conservation:** "every A must be matched by a B" (every reservation released, every charge refunded if cancelled)
- **Idempotency:** "X can happen at most once"

## After producing the decomposition

1. Show it to the user.
2. Ask if they want to adjust anything — names, splits, missing failure paths, missing invariants.
3. When approved, suggest the next step:

   > Decomposition approved. Next: run `/sdd:author-machine` for each machine, or let me dispatch subagents to implement them in parallel.

## What not to do

- Do not write Python code in this phase. Prose only.
- Do not invent scenarios — those come from the decomposition in Phase 3, not here.
- Do not over-decompose. If two "domains" share every state and event, they're one machine.
- Do not skip failure paths just because the user didn't mention them. Surface them in "Failure paths to model" and let the user prune.
- Do not omit invariants because they're "obvious" — write them down so they can be verified mechanically later.
