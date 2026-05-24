---
name: sdd-diagnose
description: Diagnose a specific SDD convergence failure — read StepResult / StepError / RoutingFailure / invariant failures, trace the cascade to the root cause, and propose a targeted fix. Use when sdd:converge has reported a failure and you need to drill into the why. Triggers include "why did this fail?", "diagnose this convergence failure", "sdd:diagnose", "trace this cascade", "what caused the invariant violation?".
---

# sdd:diagnose — Root-cause an SDD failure

You are in the diagnosis sub-loop of Phase 4. A convergence layer reported a failure; this skill drives a structured root-cause walk and proposes the fix. After diagnosis, the user (or you, with their approval) applies the fix and reruns `/sdd:converge` for regression.

## When this skill fires

- `/sdd:converge` produced failures and the cause is non-obvious
- The user has shared a `StepResult`, `ScenarioResult`, or `ConvergenceReport` and asks "why?"
- A scenario is failing inconsistently with the machine code — something doesn't line up

## Diagnostic sequence (always in this order)

### Step 1: Identify which layer failed

Read the `ConvergenceReport.summary()` or the failing `ScenarioResult`. One of:

| Layer | Diagnostic entry point |
|---|---|
| Structural (Layer 1) | `report.structural` — unreachable / terminal / dead_letters / phantom_subscriptions / guard_completeness_issues |
| Scenario (Layer 2) | `scenario_result.failure_reason` + `scenario_result.step_results[i].errors` |
| Invariant (Layer 3) | `scenario_result.invariant_results.failures` |
| Coverage gap | `report.transition_coverage`, `report.state_coverage`, `report.event_path_coverage` |

Each entry point has a different diagnostic walk below.

### Step 2: Walk by error type

#### Structural: `unreachable_states`

A state is declared but no transition targets it. Either delete the state or add the missing transition. Show the user the unreachable state and ask which.

#### Structural: `terminal_states`

A non-final state has no path to any final state. The machine could get stuck. Either mark the state `final=True` (if the intent is termination there) or add a transition to an existing final state.

#### Structural: `dead_letters`

An event is emitted but no machine subscribes. Two patterns:

- **Adapter-only event** (e.g. `order.completed` for notifications): expected, no code fix. Note in your diagnosis that this is a known acceptable dead letter.
- **Missing subscription**: a downstream machine should react. Identify which machine should subscribe by reading the decomposition (if available) or by asking the user.

#### Structural: `phantom_subscriptions`

A `subscriptions()` map declares an event name no machine emits. Almost always one of:
- Typo in the subscription event name
- Typo in the emit call
- The emitter machine was never registered

Compare the phantom names side-by-side with the dead-letter list — typos jump out visually.

#### Structural: `guard_completeness_issues`

A guarded transition (multiple targets from one source) has an explicit guard on every branch with no fallback. If all guards happen to return False, the transition fails with `no_guard_passed`.

Fix: remove the explicit guard from the last branch. Per the documented convention, the last branch acts as the fallback.

#### Scenario: `unknown_transition`

The scenario fires a transition name that doesn't exist on the target machine. Either typo or the machine doesn't have that transition yet.

#### Scenario: `invalid_source_state`

A transition was fired from a state it can't fire from. Two patterns:

- The scenario fires too early (prior step was supposed to advance the machine but didn't)
- The transition's source list is wrong

Read `step_results[i-1]` to confirm whether the prior step actually fired what was expected.

#### Scenario: `no_guard_passed`

Every guard branch returned False. Three patterns:

- The scenario feeds bad data (e.g. validate with empty items). If the test means to verify rejection, the step should use `expect_failure: no_guard_passed`.
- A guard is too strict — fix the guard.
- The transition has no fallback branch and a guard returned False unexpectedly. Make the last branch a fallback, or fix the guard.

Print the context at the point of failure to see what the guard was reading.

#### Scenario: `schema_validation_failed`

The emitter's payload doesn't match its declared `EVENT_SCHEMAS`. The error message names the missing field or type mismatch precisely.

Fix the emit call (or the schema, if the schema is wrong). This is exactly the bug class that used to surface 5 steps downstream — now it surfaces at the emit site.

#### Scenario: `routing_failures` non-empty

The cascade didn't fire because an event couldn't be routed. Read each `RoutingFailure`:

- `no_resolver_registered` — the target machine needs `runner.register_resolver(MachineClass, lambda e: e.payload.get("...")). Add to the driver.
- `resolver_returned_none` — the resolver looked up a key not in the payload. Compare `event_payload_keys` (shown in the failure) to what the resolver reads. The fix is either renaming the resolver's key OR changing the emitter to include the right key (usually the latter — match the project's existing convention).
- `resolver_raised` — the resolver lambda threw. Make it use `.get()` instead of `[]`, or guard against missing keys.

#### Invariant: `violation_details`

The system passed structural and scenario layers but violates a cross-machine rule. The invariant message names the offending instance and the property.

Diagnostic walk:
1. Identify the **scenario** that triggered the violation (the one whose `invariant_results.failures` is non-empty).
2. Read the event log filtered to that instance: `event_log.filter(source_instance="ord_X")`.
3. Find the moment the invariant was breached. For "order picked up without barista finishing", the breach is the moment of `order.completed` without a preceding `barista.finished`.
4. Determine which machine should have prevented the breach. Usually one of:
   - **Missing guard** — a transition was allowed that shouldn't have been (e.g. `pickup` from `ready` when there's no `barista.finished` precondition)
   - **Missing subscription** — a machine didn't react to a precondition event
   - **Wrong transition order** — events fired in the wrong order
5. Propose the fix. Common fixes:
   - Add a state to model the precondition (most robust — turns implicit ordering into explicit states)
   - Add a guard that reads context to check for a precondition flag
   - Reorder transitions in the scenario to expose whether the order matters

5. **After fixing, generate a regression scenario** for this exact violation. Add to `scenarios/`. This guarantees the violation can never be reintroduced silently.

#### Coverage gap

A transition / state / subscription wasn't exercised. Generate one new scenario per uncovered item. Per `docs/05`, these are systematic, not creative — one scenario per missing branch.

### Step 3: Propose the fix

Output should be:
- **Root cause** (one sentence)
- **Affected file(s)** with line references
- **The exact change** (paste the before/after diff if practical)
- **Regression scenarios** to add (if applicable)

Wait for the user's go-ahead before applying the change unless the user has set Auto Mode and the fix is unambiguous.

### Step 4: After applying the fix

> Fixed. Run `/sdd:converge` to confirm and check for regressions.

## Useful diagnostic patterns

### Find the precise moment something broke

```python
# All events for one instance, in order
list(runner.event_log.filter(source_instance="ord_1"))

# All events of one type
list(runner.event_log.filter(name="payment.captured"))

# Combined
runner.event_log.filter(source_machine="OrderLifecycle").filter(name="order.cancelled")
```

### Check what a guard was reading at failure time

If a `no_guard_passed` happened, the machine's `context` at that moment is still introspectable via the instance. But the runner's `_fire_internal` resets before guard evaluation — so the context at failure includes the kwargs that were merged in.

When in doubt, add a temporary print in the guard to log what it sees, run the scenario, then remove the print after diagnosis.

### Disambiguating "scenario wrong vs. machine wrong"

If a scenario expects state `validated` but the machine reached `cancelled`:
- Did the scenario provide valid items? Check the `setup` context block.
- Is the guard correctly identifying "valid" inputs? Read the guard body.
- Is the test of validity matching the production reality? If items=[{"sku": "A"}] should pass, the guard shouldn't require `quantity`.

Either the scenario is feeding bad data (fix the scenario) or the guard rejects good data (fix the guard). Reading the guard body usually tells you which.

## What not to do

- Do not jump straight to the most recent change as the cause. Read the failure first; let the data point you.
- Do not "fix" a scenario by removing the assertion that's failing. That makes the test useless.
- Do not silence a structural check by removing the offending machine. Find the missing piece.
- Do not add try/except in machine code to swallow a `schema_validation_failed`. Fix the payload.
- Do not skip the regression scenario after fixing an invariant. The whole point is the violation can never come back unnoticed.
