"""Tests for sdd.protocol module."""

import pytest
from sdd.protocol import State, StateMachine, TransitionResult, TransitionRecord
from sdd.events import EventBus


class OrderLifecycle(StateMachine):
    """Example state machine for testing."""

    # States
    created = State(initial=True)
    validated = State()
    fulfilled = State()
    completed = State(final=True)
    cancelled = State(final=True)

    # Transitions
    validate = created.to(validated) | created.to(cancelled)
    fulfill = validated.to(fulfilled)
    complete = fulfilled.to(completed)
    cancel = (created | validated | fulfilled).to(cancelled)

    def guard_validate_to_validated(self, **kwargs) -> bool:
        """Only valid if items and customer are present."""
        return bool(
            self.context.get("items") and self.context.get("customer_id")
        )

    def guard_validate_to_cancelled(self, **kwargs) -> bool:
        """Fallback: cancel if validation would fail."""
        return not self.guard_validate_to_validated(**kwargs)

    def on_enter_validated(self):
        """Emit event when order passes validation."""
        self.emit("order.validated", {
            "order_id": self.context.get("order_id"),
            "items": self.context.get("items"),
        })

    def on_enter_cancelled(self):
        self.emit("order.cancelled", {
            "order_id": self.context.get("order_id"),
            "reason": self.context.get("cancellation_reason", "unspecified"),
        })


class TestStateDescriptor:
    """Tests for State descriptor."""

    def test_state_has_name(self):
        """State descriptor captures its attribute name."""
        assert OrderLifecycle.created == "created"
        assert OrderLifecycle.validated == "validated"

    def test_initial_flag(self):
        """State can be marked as initial."""
        assert OrderLifecycle._states["created"].initial is True
        assert OrderLifecycle._states["validated"].initial is False

    def test_final_flag(self):
        """State can be marked as final."""
        assert OrderLifecycle._states["completed"].final is True
        assert OrderLifecycle._states["cancelled"].final is True
        assert OrderLifecycle._states["created"].final is False


class TestStateMachineValidation:
    """Tests for state machine class validation."""

    def test_requires_initial_state(self):
        """Machine must have exactly one initial state."""
        with pytest.raises(TypeError, match="must have exactly one initial state"):
            class NoInitial(StateMachine):
                running = State()
                done = State(final=True)
                go = running.to(done)

    def test_requires_final_state(self):
        """Machine must have at least one final state."""
        with pytest.raises(TypeError, match="must have at least one final state"):
            class NoFinal(StateMachine):
                start = State(initial=True)
                running = State()
                go = start.to(running)

    def test_rejects_multiple_initial_states(self):
        """Machine cannot have multiple initial states."""
        with pytest.raises(TypeError, match="multiple initial states"):
            class MultipleInitial(StateMachine):
                start1 = State(initial=True)
                start2 = State(initial=True)
                done = State(final=True)
                go = start1.to(done)


class TestStateMachineInstantiation:
    """Tests for state machine instantiation."""

    def test_can_instantiate(self):
        """Can create instance of StateMachine subclass."""
        order = OrderLifecycle()
        assert order is not None

    def test_starts_in_initial_state(self):
        """Machine starts in the initial state."""
        order = OrderLifecycle()
        assert order.current_state == "created"

    def test_context_initialization(self):
        """Machine can be initialized with context."""
        order = OrderLifecycle(context={"order_id": "123"})
        assert order.context["order_id"] == "123"

    def test_context_is_copied(self):
        """Context property returns a copy."""
        order = OrderLifecycle(context={"order_id": "123"})
        ctx = order.context
        ctx["order_id"] = "456"
        assert order.context["order_id"] == "123"


class TestTransitions:
    """Tests for firing transitions."""

    def test_fire_returns_transition_result(self):
        """fire() returns a TransitionResult."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        result = order.fire("validate")
        assert isinstance(result, TransitionResult)

    def test_successful_transition(self):
        """Transition succeeds when guard passes."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        result = order.fire("validate")
        assert result.success is True
        assert result.source == "created"
        assert result.target == "validated"
        assert result.transition == "validate"
        assert result.failure_reason is None

    def test_state_changes_after_transition(self):
        """State changes after successful transition."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        order.fire("validate")
        assert order.current_state == "validated"

    def test_unknown_transition(self):
        """Unknown transition returns failure."""
        order = OrderLifecycle()
        result = order.fire("nonexistent")
        assert result.success is False
        assert result.failure_reason == "unknown_transition"
        assert order.current_state == "created"

    def test_invalid_source_state(self):
        """Transition from wrong state returns failure."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        result = order.fire("complete")  # Can't complete from created
        assert result.success is False
        assert result.failure_reason == "invalid_source_state"

    def test_no_guard_passed(self):
        """Returns failure when no guard passes."""
        # Create a machine with guards that all return False
        class StrictMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)

            def guard_go_to_end(self, **kwargs) -> bool:
                return False  # Never passes

        machine = StrictMachine()
        result = machine.fire("go")
        assert result.success is False
        assert result.failure_reason == "no_guard_passed"

    def test_guarded_transition_first_branch(self):
        """Guarded transition takes first passing branch."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        result = order.fire("validate")
        assert result.target == "validated"

    def test_guarded_transition_second_branch(self):
        """Guarded transition takes second branch when first fails."""
        order = OrderLifecycle(context={"order_id": "123"})  # Missing items
        result = order.fire("validate")
        assert result.target == "cancelled"

    def test_kwargs_merged_into_context(self):
        """Keyword arguments are merged into context."""
        order = OrderLifecycle(context={"order_id": "123"})
        order.fire("validate", cancellation_reason="missing_data")
        assert order.context["cancellation_reason"] == "missing_data"

    def test_multi_source_transition(self):
        """Transition can fire from multiple source states."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        order.fire("validate")
        assert order.current_state == "validated"
        result = order.fire("cancel")
        assert result.success is True
        assert result.target == "cancelled"


class TestGuards:
    """Tests for guard discovery and invocation."""

    def test_guard_discovered_by_naming(self):
        """Guards are discovered by naming convention."""
        order = OrderLifecycle(context={
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        result = order.fire("validate")
        assert result.success is True
        assert result.target == "validated"

    def test_guard_receives_kwargs(self):
        """Guard receives transition keyword arguments."""
        class TestMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)
            received_kwargs = None

            def guard_go_to_end(self, **kwargs) -> bool:
                TestMachine.received_kwargs = kwargs
                return True

        machine = TestMachine()
        machine.fire("go", foo="bar", count=42)
        assert TestMachine.received_kwargs == {"foo": "bar", "count": 42}

    def test_default_guard_allows_transition(self):
        """Transition without explicit guard is allowed."""
        class SimpleMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)

        machine = SimpleMachine()
        result = machine.fire("go")
        assert result.success is True


class TestSideEffects:
    """Tests for on_enter and on_exit side effects."""

    def test_on_enter_called(self):
        """on_enter_{state} is called when entering a state."""
        entered = []

        class TestMachine(StateMachine):
            start = State(initial=True)
            middle = State()
            end = State(final=True)
            go = start.to(middle)
            finish = middle.to(end)

            def on_enter_middle(self):
                entered.append("middle")

        machine = TestMachine()
        machine.fire("go")
        assert "middle" in entered

    def test_on_exit_called(self):
        """on_exit_{state} is called when leaving a state."""
        exited = []

        class TestMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)

            def on_exit_start(self):
                exited.append("start")

        machine = TestMachine()
        machine.fire("go")
        assert "start" in exited

    def test_on_transition_called(self):
        """on_transition_{name} is called when transition fires."""
        called = []

        class TestMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)

            def on_transition_go(self):
                called.append("go")

        machine = TestMachine()
        machine.fire("go")
        assert "go" in called

    def test_side_effect_order(self):
        """Side effects execute in order: exit, transition, enter."""
        order = []

        class TestMachine(StateMachine):
            start = State(initial=True)
            end = State(final=True)
            go = start.to(end)

            def on_exit_start(self):
                order.append("exit")

            def on_transition_go(self):
                order.append("transition")

            def on_enter_end(self):
                order.append("enter")

        machine = TestMachine()
        machine.fire("go")
        assert order == ["exit", "transition", "enter"]


class TestEventEmission:
    """Tests for event emission via self.emit()."""

    def test_emit_creates_event(self):
        """emit() creates an event."""
        bus = EventBus()
        order = OrderLifecycle(
            context={
                "order_id": "123",
                "customer_id": "cust_1",
                "items": [{"sku": "A1"}],
            },
            event_bus=bus,
        )
        result = order.fire("validate")
        assert len(result.events_emitted) == 1
        event = result.events_emitted[0]
        assert event.name == "order.validated"
        assert event.payload["order_id"] == "123"

    def test_events_appear_in_bus_log(self):
        """Emitted events appear in EventBus.log."""
        bus = EventBus()
        order = OrderLifecycle(
            context={
                "order_id": "123",
                "customer_id": "cust_1",
                "items": [{"sku": "A1"}],
            },
            event_bus=bus,
        )
        order.fire("validate")
        assert len(bus.log) == 1
        assert bus.log[0].name == "order.validated"

    def test_event_source_machine(self):
        """Event has correct source_machine."""
        bus = EventBus()
        order = OrderLifecycle(
            context={
                "order_id": "123",
                "customer_id": "cust_1",
                "items": [{"sku": "A1"}],
            },
            event_bus=bus,
            instance_id="order_123",
        )
        order.fire("validate")
        event = bus.log[0]
        assert event.source_machine == "OrderLifecycle"
        assert event.source_instance == "order_123"


class TestIntrospection:
    """Tests for machine introspection API."""

    def test_current_state(self):
        """current_state returns the current state name."""
        order = OrderLifecycle()
        assert order.current_state == "created"

    def test_is_final(self):
        """is_final returns whether current state is final."""
        order = OrderLifecycle(context={"order_id": "123"})
        assert order.is_final is False
        order.fire("validate")  # Goes to cancelled
        assert order.is_final is True

    def test_available_transitions(self):
        """available_transitions returns valid transitions from current state."""
        order = OrderLifecycle()
        assert set(order.available_transitions) == {"validate", "cancel"}

    def test_available_transitions_empty_when_final(self):
        """No transitions available from final state."""
        order = OrderLifecycle(context={"order_id": "123"})
        order.fire("validate")  # Goes to cancelled
        assert order.available_transitions == []

    def test_all_states(self):
        """all_states returns all declared states."""
        order = OrderLifecycle()
        expected = {"created", "validated", "fulfilled", "completed", "cancelled"}
        assert set(order.all_states) == expected

    def test_all_transitions(self):
        """all_transitions returns transition info."""
        order = OrderLifecycle()
        transitions = order.all_transitions
        assert any(t["name"] == "validate" for t in transitions)
        assert any(t["name"] == "cancel" for t in transitions)

    def test_history(self):
        """history tracks fired transitions."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        order.fire("validate")
        assert len(order.history) == 1
        record = order.history[0]
        assert record.transition == "validate"
        assert record.source == "created"
        assert record.target == "validated"


class TestSerialization:
    """Tests for snapshot() and restore()."""

    def test_snapshot(self):
        """snapshot() returns machine state as dict."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        order.fire("validate")
        snapshot = order.snapshot()
        assert snapshot["state"] == "validated"
        assert snapshot["context"]["order_id"] == "123"
        assert len(snapshot["history"]) == 1

    def test_restore(self):
        """restore() reconstitutes machine from snapshot."""
        order = OrderLifecycle(context={
            "order_id": "123",
            "customer_id": "cust_1",
            "items": [{"sku": "A1"}],
        })
        order.fire("validate")
        snapshot = order.snapshot()

        restored = OrderLifecycle.restore(snapshot)
        assert restored.current_state == "validated"
        assert restored.context["order_id"] == "123"
        assert len(restored.history) == 1

    def test_restore_with_event_bus(self):
        """restore() can accept an event bus."""
        snapshot = {
            "state": "validated",
            "context": {"order_id": "123"},
            "history": [],
        }
        bus = EventBus()
        restored = OrderLifecycle.restore(snapshot, event_bus=bus)
        assert restored._event_bus is bus


class TestSubscriptions:
    """Tests for event subscriptions."""

    def test_default_subscriptions_empty(self):
        """Default subscriptions() returns empty dict."""
        assert OrderLifecycle.subscriptions() == {}

    def test_custom_subscriptions(self):
        """Custom subscriptions can be defined."""
        class InventoryState(StateMachine):
            available = State(initial=True)
            reserved = State()
            released = State(final=True)
            reserve = available.to(reserved)
            release = reserved.to(released)

            @classmethod
            def subscriptions(cls) -> dict[str, str]:
                return {
                    "order.validated": "reserve",
                    "order.cancelled": "release",
                }

        subs = InventoryState.subscriptions()
        assert subs["order.validated"] == "reserve"
        assert subs["order.cancelled"] == "release"
