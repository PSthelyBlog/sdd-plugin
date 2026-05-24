# Scenario Language

## Purpose

Scenarios are the primary interface between what the user wants and what the simulation verifies. This document defines how scenarios are expressed, decomposed into executable steps, and used for coverage analysis and replay.

## Scenario Levels

Scenarios exist at two levels of abstraction. Claude Code translates between them.

### Narrative Scenarios

Narrative scenarios are human-readable descriptions of system behavior. They come from the user's task description or from Claude Code's domain analysis. They describe **what happens** without specifying machine-level details.

```yaml
narrative: "Customer upgrades mid-cycle"
description: >
  A customer with an active monthly subscription upgrades from Basic to Pro
  halfway through their billing cycle. They are charged a prorated amount
  for the remainder of the current period, and the next full billing cycle
  uses the new plan rate.
```

Narrative scenarios serve as documentation and acceptance criteria. The user reads and approves them.

### Executable Scenarios

Executable scenarios are sequences of `Step` objects that the simulation runner can process directly. Each step creates an instance, fires a transition, advances time, or asserts a condition.

```yaml
scenario: customer_upgrades_mid_cycle
narrative: "Customer upgrades mid-cycle"

setup:
  - create: SubscriptionLifecycle("sub_1")
    context:
      subscription_id: "sub_1"
      customer_id: "cust_1"
      plan: "basic"
      monthly_rate: 9.99

steps:
  - fire: SubscriptionLifecycle("sub_1").activate
  - fire: BillingCycle("sub_1_cycle_1").initiate
    args: {amount: 9.99}
  - fire: PaymentAttempt("sub_1_pay_1").process
  - fire: PaymentAttempt("sub_1_pay_1").succeed
    args: {transaction_id: "tx_001"}
  - advance_time: {days: 15}
  - fire: PlanManagement("sub_1").change
    args: {new_plan: "pro", new_rate: 29.99}
  - assert:
      events_emitted: ["plan.changed"]
      context:
        PlanManagement("sub_1"):
          prorated_amount: 10.00  # roughly half of the difference

  # Next billing cycle should use new rate
  - advance_time: {days: 15}
  - fire: BillingCycle("sub_1_cycle_2").initiate
    args: {amount: 29.99}
  - fire: PaymentAttempt("sub_1_pay_2").process
  - fire: PaymentAttempt("sub_1_pay_2").succeed
    args: {transaction_id: "tx_002"}

expect:
  final_states:
    SubscriptionLifecycle("sub_1"): active
    BillingCycle("sub_1_cycle_1"): completed
    BillingCycle("sub_1_cycle_2"): completed
    PlanManagement("sub_1"): applied
  event_sequence_includes:
    - billing.charge_initiated
    - payment.succeeded
    - plan.changed
    - plan.proration_charged
    - billing.charge_initiated
    - payment.succeeded
```

## Step Types

### `create`

Instantiate a machine with initial context.

```yaml
- create: MachineName("instance_id")
  context:
    field: value
```

### `fire`

Fire a transition on a machine instance with optional arguments.

```yaml
- fire: MachineName("instance_id").transition_name
  args:
    key: value
```

### `advance_time`

Move the virtual clock forward, triggering any pending timeouts.

```yaml
- advance_time: {hours: 24}
- advance_time: {days: 7}
- advance_time: {minutes: 30}
```

### `assert`

Mid-scenario assertion. Checked immediately when reached.

```yaml
- assert:
    states:
      MachineName("instance_id"): expected_state
    events_emitted: ["event.name"]
    events_not_emitted: ["other.event"]
    context:
      MachineName("instance_id"):
        field: expected_value
```

Mid-scenario assertions are useful for verifying intermediate states. If an assertion fails, the scenario halts at that point and reports the failure.

### `expect_failure`

Assert that a transition **should fail**. Used for testing rejection paths.

```yaml
- fire: PaymentAttempt("pay_1").process
  expect_failure: no_guard_passed
```

This step succeeds if the transition fails with the specified reason, and fails if the transition succeeds.

## Scenario Metadata

Each scenario carries metadata for organization and coverage tracking:

```yaml
scenario: payment_retry_exhaustion
narrative: "Payment fails after all retry attempts"
category: failure_path         # happy_path | failure_path | timeout | edge_case
priority: high                 # high | medium | low
machines_involved:
  - BillingCycle
  - PaymentAttempt
  - SubscriptionLifecycle
transitions_targeted:
  - PaymentAttempt.fail
  - BillingCycle.retry
  - BillingCycle.escalate
  - SubscriptionLifecycle.suspend
```

Claude Code uses metadata to verify coverage completeness. If a transition appears in no scenario's `transitions_targeted`, Claude Code generates a scenario to exercise it.

## Scenario Derivation Rules

### From User Task

Claude Code extracts scenarios from the user's task description using these heuristics:

**Verbs become transitions.** "Customer places an order" → a transition on `OrderLifecycle`.

**Conditional language becomes branches.** "If payment fails..." → a failure scenario branching from the payment transition.

**Temporal language becomes timeout scenarios.** "After 3 days..." → a time-advance step followed by a timeout transition.

**Negative requirements become invariants.** "An order must never ship without payment" → a property invariant, plus a scenario that attempts the violation to prove the invariant holds.

### From Machine Structure

Systematic scenario generation ensures baseline coverage:

**One happy path per machine.** A scenario that takes the machine from initial to a successful final state, exercising the most common transition sequence.

**One failure path per guarded branch.** For each `|` in a transition declaration, a scenario that forces that specific branch.

**One timeout path per timeout decorator.** A scenario that advances time past the deadline and verifies the timeout transition fires.

### From Invariant Violations

When an invariant fails, Claude Code reverse-engineers a minimal scenario that triggers the violation. This scenario becomes permanent, ensuring the violation is never reintroduced.

## Scenario Composition

Complex scenarios can be composed from reusable fragments:

```yaml
fragments:
  create_active_subscription:
    - create: SubscriptionLifecycle("sub_1")
      context: {subscription_id: "sub_1", plan: "basic", monthly_rate: 9.99}
    - fire: SubscriptionLifecycle("sub_1").activate

  successful_billing:
    - fire: BillingCycle("{cycle_id}").initiate
      args: {amount: "{amount}"}
    - fire: PaymentAttempt("{pay_id}").process
    - fire: PaymentAttempt("{pay_id}").succeed
      args: {transaction_id: "{tx_id}"}

scenario: three_successful_months
steps:
  - use: create_active_subscription
  - use: successful_billing
    bind: {cycle_id: "c1", pay_id: "p1", amount: 9.99, tx_id: "tx_001"}
  - advance_time: {days: 30}
  - use: successful_billing
    bind: {cycle_id: "c2", pay_id: "p2", amount: 9.99, tx_id: "tx_002"}
  - advance_time: {days: 30}
  - use: successful_billing
    bind: {cycle_id: "c3", pay_id: "p3", amount: 9.99, tx_id: "tx_003"}
```

Fragments reduce duplication and make scenarios easier to read. The `bind` mechanism substitutes values into fragment placeholders.

## Replay

Any scenario can be replayed at any time against the current machine definitions. Replay is the primary regression testing mechanism.

When a machine is modified, Claude Code replays **all** scenarios, not just the ones targeting the changed machine. This catches cascading effects where a change in one machine breaks another machine's expectations through the event bus.

Replay produces the same `StepResult` and `Event` log as the original run. Claude Code diffs the results to identify regressions:

```
REPLAY DIFF: scenario "happy_path_order"
  Step 3: PaymentFlow("ord_1").authorize
    Previous: succeeded → state "authorized"
    Current:  failed → "no_guard_passed"
    
  Root cause: Guard on authorize now requires "billing_address" in context,
  which this scenario does not provide.
```

Replay diffs give Claude Code precise information about what broke and why, enabling targeted fixes.
