"""Tests for the OrderLifecycle reference machine."""

import pytest

from machines.order_lifecycle import OrderLifecycle
from sdd.events import EventBus


class TestOrderLifecycleStructure:
    """Verify machine structure matches specification."""

    def test_has_correct_states(self):
        """Machine should have all specified states."""
        states = OrderLifecycle._states
        assert "created" in states
        assert "validated" in states
        assert "fulfilled" in states
        assert "completed" in states
        assert "cancelled" in states

    def test_initial_state(self):
        """created should be the initial state."""
        assert OrderLifecycle._states["created"].initial is True
        machine = OrderLifecycle()
        assert machine.current_state == "created"

    def test_final_states(self):
        """completed and cancelled should be final states."""
        assert OrderLifecycle._states["completed"].final is True
        assert OrderLifecycle._states["cancelled"].final is True

    def test_has_correct_transitions(self):
        """Machine should have all specified transitions."""
        transitions = OrderLifecycle._transitions
        assert "validate" in transitions
        assert "fulfill" in transitions
        assert "complete" in transitions
        assert "cancel" in transitions

    def test_validate_transition_branches(self):
        """validate should branch to validated or cancelled."""
        transition = OrderLifecycle._transitions["validate"]
        branches = transition.get_branches_from("created")
        targets = {b.target for b in branches}
        assert targets == {"validated", "cancelled"}

    def test_cancel_multi_source(self):
        """cancel should be available from created, validated, and fulfilled."""
        transition = OrderLifecycle._transitions["cancel"]
        sources = transition.get_all_sources()
        assert sources == {"created", "validated", "fulfilled"}


class TestHappyPath:
    """Test the successful order flow: created → validated → fulfilled → completed."""

    @pytest.fixture
    def order_context(self):
        return {
            "order_id": "ORD-001",
            "customer_id": "CUST-123",
            "items": [
                {"sku": "ITEM-A", "quantity": 2},
                {"sku": "ITEM-B", "quantity": 1},
            ],
        }

    @pytest.fixture
    def machine_with_bus(self, order_context):
        bus = EventBus()
        machine = OrderLifecycle(
            context=order_context,
            event_bus=bus,
            instance_id="order-001",
        )
        return machine, bus

    def test_full_happy_path(self, machine_with_bus):
        """Order completes successfully through all states."""
        machine, bus = machine_with_bus

        # Validate
        result = machine.fire("validate")
        assert result.success is True
        assert result.target == "validated"
        assert machine.current_state == "validated"

        # Fulfill
        result = machine.fire("fulfill", warehouse_id="WH-EAST")
        assert result.success is True
        assert result.target == "fulfilled"
        assert machine.current_state == "fulfilled"

        # Complete
        result = machine.fire("complete")
        assert result.success is True
        assert result.target == "completed"
        assert machine.current_state == "completed"
        assert machine.is_final is True

    def test_events_emitted_on_happy_path(self, machine_with_bus):
        """Correct events are emitted at each stage."""
        machine, bus = machine_with_bus

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-EAST")
        machine.fire("complete")

        log = bus.log
        assert len(log) == 3

        # Check order.validated event
        validated_events = log.filter(name="order.validated").to_list()
        assert len(validated_events) == 1
        assert validated_events[0].payload["order_id"] == "ORD-001"
        assert validated_events[0].payload["customer_id"] == "CUST-123"
        assert len(validated_events[0].payload["items"]) == 2

        # Check order.fulfilled event
        fulfilled_events = log.filter(name="order.fulfilled").to_list()
        assert len(fulfilled_events) == 1
        assert fulfilled_events[0].payload["order_id"] == "ORD-001"
        assert fulfilled_events[0].payload["warehouse_id"] == "WH-EAST"

        # Check order.completed event
        completed_events = log.filter(name="order.completed").to_list()
        assert len(completed_events) == 1
        assert completed_events[0].payload["order_id"] == "ORD-001"

    def test_no_transitions_from_completed(self, machine_with_bus):
        """Once completed, no more transitions are available."""
        machine, bus = machine_with_bus

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-EAST")
        machine.fire("complete")

        assert machine.available_transitions == []

        # Attempting cancel should fail
        result = machine.fire("cancel", cancellation_reason="test")
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"


class TestValidationFailure:
    """Test order validation failure path."""

    def test_empty_items_fails_validation(self):
        """Order with no items should fail validation."""
        machine = OrderLifecycle(
            context={"order_id": "ORD-002", "customer_id": "CUST-456", "items": []},
            event_bus=EventBus(),
        )

        result = machine.fire("validate")
        assert result.success is True
        assert result.target == "cancelled"
        assert machine.current_state == "cancelled"

    def test_invalid_item_quantity_fails_validation(self):
        """Order with zero quantity items should fail validation."""
        machine = OrderLifecycle(
            context={
                "order_id": "ORD-003",
                "items": [{"sku": "ITEM-A", "quantity": 0}],
            },
            event_bus=EventBus(),
        )

        result = machine.fire("validate")
        assert result.success is True
        assert result.target == "cancelled"

    def test_missing_sku_fails_validation(self):
        """Order item without SKU should fail validation."""
        machine = OrderLifecycle(
            context={
                "order_id": "ORD-004",
                "items": [{"quantity": 5}],
            },
            event_bus=EventBus(),
        )

        result = machine.fire("validate")
        assert result.success is True
        assert result.target == "cancelled"

    def test_cancelled_event_on_validation_failure(self):
        """order.cancelled event should be emitted on validation failure."""
        bus = EventBus()
        machine = OrderLifecycle(
            context={"order_id": "ORD-005", "items": []},
            event_bus=bus,
        )

        machine.fire("validate")

        cancelled_events = bus.log.filter(name="order.cancelled").to_list()
        assert len(cancelled_events) == 1
        assert cancelled_events[0].payload["order_id"] == "ORD-005"


class TestCancellation:
    """Test cancellation from various states."""

    @pytest.fixture
    def valid_context(self):
        return {
            "order_id": "ORD-100",
            "customer_id": "CUST-100",
            "items": [{"sku": "ITEM-X", "quantity": 1}],
        }

    def test_cancel_from_created(self, valid_context):
        """Order can be cancelled from created state."""
        bus = EventBus()
        machine = OrderLifecycle(context=valid_context, event_bus=bus)

        result = machine.fire("cancel", cancellation_reason="customer_request")
        assert result.success is True
        assert machine.current_state == "cancelled"
        assert machine.is_final is True

        cancelled_events = bus.log.filter(name="order.cancelled").to_list()
        assert len(cancelled_events) == 1
        assert cancelled_events[0].payload["reason"] == "customer_request"

    def test_cancel_from_validated(self, valid_context):
        """Order can be cancelled from validated state."""
        bus = EventBus()
        machine = OrderLifecycle(context=valid_context, event_bus=bus)

        machine.fire("validate")
        result = machine.fire("cancel", cancellation_reason="out_of_stock")

        assert result.success is True
        assert machine.current_state == "cancelled"

        cancelled_events = bus.log.filter(name="order.cancelled").to_list()
        assert len(cancelled_events) == 1
        assert cancelled_events[0].payload["reason"] == "out_of_stock"

    def test_cancel_from_fulfilled(self, valid_context):
        """Order can be cancelled from fulfilled state."""
        bus = EventBus()
        machine = OrderLifecycle(context=valid_context, event_bus=bus)

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-WEST")
        result = machine.fire("cancel", cancellation_reason="shipping_error")

        assert result.success is True
        assert machine.current_state == "cancelled"

    def test_cannot_cancel_from_completed(self, valid_context):
        """Completed orders cannot be cancelled."""
        machine = OrderLifecycle(context=valid_context, event_bus=EventBus())

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-WEST")
        machine.fire("complete")

        result = machine.fire("cancel", cancellation_reason="too_late")
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"
        assert machine.current_state == "completed"

    def test_cannot_cancel_from_cancelled(self, valid_context):
        """Already cancelled orders cannot be cancelled again."""
        machine = OrderLifecycle(context=valid_context, event_bus=EventBus())

        machine.fire("cancel", cancellation_reason="first")
        result = machine.fire("cancel", cancellation_reason="second")

        assert result.success is False
        assert result.failure_reason == "invalid_source_state"

    def test_default_cancellation_reason(self, valid_context):
        """Default cancellation reason is 'unspecified'."""
        bus = EventBus()
        machine = OrderLifecycle(context=valid_context, event_bus=bus)

        machine.fire("cancel")  # No reason provided

        cancelled_events = bus.log.filter(name="order.cancelled").to_list()
        assert cancelled_events[0].payload["reason"] == "unspecified"


class TestInvalidTransitions:
    """Test error handling for invalid transitions."""

    @pytest.fixture
    def machine(self):
        return OrderLifecycle(
            context={
                "order_id": "ORD-ERR",
                "items": [{"sku": "X", "quantity": 1}],
            }
        )

    def test_unknown_transition(self, machine):
        """Unknown transition returns failure."""
        result = machine.fire("nonexistent")
        assert result.success is False
        assert result.failure_reason == "unknown_transition"

    def test_fulfill_from_created(self, machine):
        """Cannot fulfill directly from created."""
        result = machine.fire("fulfill", warehouse_id="WH-1")
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"

    def test_complete_from_created(self, machine):
        """Cannot complete directly from created."""
        result = machine.fire("complete")
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"

    def test_complete_from_validated(self, machine):
        """Cannot complete directly from validated (must fulfill first)."""
        machine.fire("validate")
        result = machine.fire("complete")
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"


class TestSnapshotRestore:
    """Test serialization and deserialization."""

    def test_snapshot_captures_state(self):
        """Snapshot captures current state and context."""
        context = {
            "order_id": "ORD-SNAP",
            "customer_id": "CUST-SNAP",
            "items": [{"sku": "SNAP-1", "quantity": 3}],
        }
        machine = OrderLifecycle(context=context, event_bus=EventBus())
        machine.fire("validate")

        snapshot = machine.snapshot()

        assert snapshot["state"] == "validated"
        assert snapshot["context"]["order_id"] == "ORD-SNAP"
        assert len(snapshot["history"]) == 1

    def test_restore_from_snapshot(self):
        """Machine can be restored from snapshot."""
        context = {
            "order_id": "ORD-RESTORE",
            "items": [{"sku": "R-1", "quantity": 1}],
        }
        original = OrderLifecycle(context=context, event_bus=EventBus())
        original.fire("validate")
        original.fire("fulfill", warehouse_id="WH-RESTORE")

        snapshot = original.snapshot()

        bus = EventBus()
        restored = OrderLifecycle.restore(snapshot, event_bus=bus, instance_id="restored")

        assert restored.current_state == "fulfilled"
        assert restored.context["warehouse_id"] == "WH-RESTORE"
        assert len(restored.history) == 2

    def test_restored_machine_can_continue(self):
        """Restored machine can continue processing."""
        context = {
            "order_id": "ORD-CONTINUE",
            "items": [{"sku": "C-1", "quantity": 1}],
        }
        original = OrderLifecycle(context=context, event_bus=EventBus())
        original.fire("validate")

        snapshot = original.snapshot()
        bus = EventBus()
        restored = OrderLifecycle.restore(snapshot, event_bus=bus)

        # Continue from validated state
        result = restored.fire("fulfill", warehouse_id="WH-NEW")
        assert result.success is True
        assert restored.current_state == "fulfilled"

        result = restored.fire("complete")
        assert result.success is True
        assert restored.current_state == "completed"

        # Check events were emitted
        assert len(bus.log) == 2


class TestContextUpdates:
    """Test that kwargs merge into context correctly."""

    def test_fulfill_adds_warehouse_id(self):
        """fulfill transition adds warehouse_id to context."""
        machine = OrderLifecycle(
            context={
                "order_id": "ORD-CTX",
                "items": [{"sku": "CTX-1", "quantity": 1}],
            }
        )

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-CONTEXT-TEST")

        assert machine.context["warehouse_id"] == "WH-CONTEXT-TEST"

    def test_cancel_adds_reason(self):
        """cancel transition adds cancellation_reason to context."""
        machine = OrderLifecycle(
            context={
                "order_id": "ORD-REASON",
                "items": [{"sku": "R-1", "quantity": 1}],
            }
        )

        machine.fire("cancel", cancellation_reason="test_reason")

        assert machine.context["cancellation_reason"] == "test_reason"


class TestHistory:
    """Test transition history tracking."""

    def test_history_tracks_transitions(self):
        """History records all fired transitions."""
        machine = OrderLifecycle(
            context={
                "order_id": "ORD-HIST",
                "items": [{"sku": "H-1", "quantity": 1}],
            },
            event_bus=EventBus(),
        )

        machine.fire("validate")
        machine.fire("fulfill", warehouse_id="WH-1")
        machine.fire("complete")

        history = machine.history
        assert len(history) == 3

        assert history[0].transition == "validate"
        assert history[0].source == "created"
        assert history[0].target == "validated"

        assert history[1].transition == "fulfill"
        assert history[1].kwargs["warehouse_id"] == "WH-1"

        assert history[2].transition == "complete"
        assert history[2].target == "completed"
