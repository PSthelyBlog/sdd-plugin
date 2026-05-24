"""Reference invariants for OrderLifecycle validation.

These invariants demonstrate the invariant API and validate cross-machine
properties that emerge from the order processing flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdd.events import EventLog


def invariant_completed_orders_have_validated_event(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """An order cannot complete unless it was validated first.

    This ensures the order went through proper validation before
    reaching the completed state.
    """
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "completed":
            validated_events = log.filter(
                name="order.validated",
                source_instance=instance,
            )
            assert len(validated_events) > 0, (
                f"Order {instance} completed without validation event"
            )


def invariant_completed_orders_have_fulfilled_event(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """An order cannot complete unless it was fulfilled first.

    This ensures the order went through fulfillment before
    reaching the completed state.
    """
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "completed":
            fulfilled_events = log.filter(
                name="order.fulfilled",
                source_instance=instance,
            )
            assert len(fulfilled_events) > 0, (
                f"Order {instance} completed without fulfillment event"
            )


def invariant_cancelled_orders_have_reason(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """A cancelled order must have a cancellation event with a reason.

    This ensures that cancellation reasons are always recorded for
    audit and analysis purposes.
    """
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "cancelled":
            cancelled_events = log.filter(
                name="order.cancelled",
                source_instance=instance,
            )
            assert len(cancelled_events) > 0, (
                f"Order {instance} in cancelled state but no cancellation event found"
            )


def invariant_no_events_after_completion(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """No order events should be emitted after the order is completed.

    Once an order reaches a final state (completed/cancelled), no further
    events should be emitted for that order.
    """
    for (machine, instance), state in states.items():
        if machine == "OrderLifecycle" and state == "completed":
            # Get the completion event
            completed_events = list(log.filter(
                name="order.completed",
                source_instance=instance,
            ))
            if not completed_events:
                continue

            completion_time = completed_events[-1].timestamp

            # Check for any order events after completion
            all_order_events = log.filter(source_instance=instance)
            events_after = [
                e for e in all_order_events
                if e.timestamp > completion_time and e.source_machine == "OrderLifecycle"
            ]

            assert len(events_after) == 0, (
                f"Order {instance} has {len(events_after)} events after completion: "
                f"{[e.name for e in events_after]}"
            )


def invariant_single_completion_event(
    log: "EventLog", states: dict[tuple[str, str], str]
) -> None:
    """Each order can only be completed once.

    The order.completed event should occur at most once per order.
    """
    # Get all unique order instances
    order_instances = log.unique_instances("OrderLifecycle")

    for instance in order_instances:
        completed_events = log.filter(
            name="order.completed",
            source_instance=instance,
        )
        assert len(completed_events) <= 1, (
            f"Order {instance} was completed {len(completed_events)} times"
        )
