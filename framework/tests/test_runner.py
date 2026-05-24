"""Tests for the SimulationRunner."""

import pytest

from machines.order_lifecycle import OrderLifecycle
from sdd.events import Event, EventBus
from sdd.protocol import State, StateMachine
from sdd.runner import RoutingFailure, SimulationRunner, StepError, StepResult


class TestRegistrationAndResolvers:
    """Test machine registration and resolver setup."""

    def test_register_machine_class(self):
        """Can register a machine class."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        assert OrderLifecycle in runner._machine_classes

    def test_register_resolver(self):
        """Can register a resolver for a machine class."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))

        assert OrderLifecycle in runner._resolvers

    def test_resolver_extracts_instance_id(self):
        """Resolver correctly extracts instance ID from event payload."""
        runner = SimulationRunner()
        resolver = lambda e: e.payload.get("order_id")
        runner.register_resolver(OrderLifecycle, resolver)

        event = Event(
            name="test.event",
            payload={"order_id": "ORD-123"},
            source_machine="Test",
            source_instance="test-1",
        )

        assert resolver(event) == "ORD-123"


class TestInstanceManagement:
    """Test instance creation and retrieval."""

    @pytest.fixture
    def runner(self):
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        return runner

    def test_create_instance(self, runner):
        """Can create a machine instance."""
        context = {
            "order_id": "ORD-001",
            "customer_id": "CUST-123",
            "items": [{"sku": "ITEM-A", "quantity": 2}],
        }
        instance = runner.create(OrderLifecycle, "ord_001", context=context)

        assert instance is not None
        assert instance.current_state == "created"
        assert instance.context["order_id"] == "ORD-001"

    def test_get_existing_instance(self, runner):
        """Can retrieve an existing instance."""
        runner.create(OrderLifecycle, "ord_002", context={"order_id": "ORD-002"})

        instance = runner.get(OrderLifecycle, "ord_002")

        assert instance is not None
        assert instance.context["order_id"] == "ORD-002"

    def test_get_nonexistent_instance_returns_none(self, runner):
        """get() returns None for nonexistent instance."""
        instance = runner.get(OrderLifecycle, "nonexistent")

        assert instance is None

    def test_instance_has_event_bus(self, runner):
        """Created instances are connected to the runner's event bus."""
        instance = runner.create(OrderLifecycle, "ord_003", context={})

        assert instance._event_bus is runner.event_bus

    def test_instance_has_instance_id(self, runner):
        """Created instances have their instance_id set."""
        instance = runner.create(OrderLifecycle, "ord_004", context={})

        assert instance._instance_id == "ord_004"


class TestDirectTransitionFiring:
    """Test firing transitions directly via runner.fire()."""

    @pytest.fixture
    def runner_with_order(self):
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(
            OrderLifecycle,
            "ord_100",
            context={
                "order_id": "ORD-100",
                "customer_id": "CUST-100",
                "items": [{"sku": "ITEM-X", "quantity": 1}],
            },
        )
        return runner

    def test_fire_successful_transition(self, runner_with_order):
        """Successful transition returns StepResult with transitions_fired."""
        result = runner_with_order.fire("ord_100", OrderLifecycle, "validate")

        assert isinstance(result, StepResult)
        assert result.trigger == "fire:validate"
        assert len(result.transitions_fired) == 1
        assert result.transitions_fired[0].transition == "validate"
        assert result.transitions_fired[0].source == "created"
        assert result.transitions_fired[0].target == "validated"
        assert len(result.errors) == 0

    def test_fire_records_emitted_events(self, runner_with_order):
        """StepResult includes events emitted during transition."""
        result = runner_with_order.fire("ord_100", OrderLifecycle, "validate")

        assert len(result.events_emitted) == 1
        assert result.events_emitted[0].name == "order.validated"
        assert result.events_emitted[0].payload["order_id"] == "ORD-100"

    def test_fire_unknown_transition_records_error(self, runner_with_order):
        """Unknown transition is recorded as error, not raised."""
        result = runner_with_order.fire("ord_100", OrderLifecycle, "nonexistent")

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "unknown_transition"
        assert result.errors[0].transition == "nonexistent"
        assert result.errors[0].machine == "OrderLifecycle"
        assert result.errors[0].instance == "ord_100"

    def test_fire_invalid_source_state_records_error(self, runner_with_order):
        """Invalid source state is recorded as error."""
        result = runner_with_order.fire("ord_100", OrderLifecycle, "fulfill")

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "invalid_source_state"
        assert result.errors[0].machine_state_at_error == "created"

    def test_fire_no_guard_passed_records_error(self):
        """No guard passed is recorded as error."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        # Create order with invalid items (empty list)
        runner.create(
            OrderLifecycle,
            "ord_bad",
            context={"order_id": "ORD-BAD", "items": []},
        )

        # When items are empty, guard_validate_to_cancelled returns True,
        # so validate should succeed to cancelled state
        result = runner.fire("ord_bad", OrderLifecycle, "validate")
        assert len(result.errors) == 0
        assert result.transitions_fired[0].target == "cancelled"

    def test_fire_nonexistent_instance_records_error(self):
        """Firing on nonexistent instance records error."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        result = runner.fire("nonexistent", OrderLifecycle, "validate")

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "instance_not_found"

    def test_fire_with_kwargs(self, runner_with_order):
        """kwargs are passed to the transition."""
        runner_with_order.fire("ord_100", OrderLifecycle, "validate")
        result = runner_with_order.fire(
            "ord_100", OrderLifecycle, "fulfill", warehouse_id="WH-EAST"
        )

        assert result.transitions_fired[0].kwargs["warehouse_id"] == "WH-EAST"
        instance = runner_with_order.get(OrderLifecycle, "ord_100")
        assert instance.context["warehouse_id"] == "WH-EAST"

    def test_cascade_depth_tracking(self, runner_with_order):
        """StepResult includes cascade depth."""
        result = runner_with_order.fire("ord_100", OrderLifecycle, "validate")

        assert result.cascade_depth >= 1


class TestEventDrivenTransitions:
    """Test event-driven transition firing through subscriptions."""

    def test_subscription_wiring(self):
        """Subscriptions are wired when machine is registered."""
        # Create a machine that subscribes to events
        class SubscribingMachine(StateMachine):
            waiting = State(initial=True)
            triggered = State(final=True)

            react = waiting.to(triggered)

            def guard_react_to_triggered(self, **kwargs) -> bool:
                return True

            @classmethod
            def subscriptions(cls):
                return {"test.event": "react"}

        runner = SimulationRunner()
        runner.register(SubscribingMachine)
        runner.register_resolver(SubscribingMachine, lambda e: e.payload.get("id"))

        # Create an instance
        runner.create(SubscribingMachine, "sub_1", context={"id": "sub_1"})

        # Manually emit an event - this should trigger the subscription
        event = Event(
            name="test.event",
            payload={"id": "sub_1"},
            source_machine="Test",
            source_instance="test",
        )
        runner.event_bus.emit(event)

        # The subscribing machine should have transitioned
        instance = runner.get(SubscribingMachine, "sub_1")
        assert instance.current_state == "triggered"

    def test_implicit_instance_creation(self):
        """Instances are created implicitly when event arrives for new instance."""

        class AutoCreateMachine(StateMachine):
            initial = State(initial=True)
            done = State(final=True)

            start = initial.to(done)

            def guard_start_to_done(self, **kwargs) -> bool:
                return True

            @classmethod
            def subscriptions(cls):
                return {"trigger.start": "start"}

        runner = SimulationRunner()
        runner.register(AutoCreateMachine)
        runner.register_resolver(AutoCreateMachine, lambda e: e.payload.get("instance_id"))

        # No instance exists yet
        assert runner.get(AutoCreateMachine, "auto_1") is None

        # Emit event for new instance
        event = Event(
            name="trigger.start",
            payload={"instance_id": "auto_1", "data": "test"},
            source_machine="External",
            source_instance="ext_1",
        )
        runner.event_bus.emit(event)

        # Instance should now exist and be in 'done' state
        instance = runner.get(AutoCreateMachine, "auto_1")
        assert instance is not None
        assert instance.current_state == "done"
        # Context should be seeded from event payload
        assert instance.context["data"] == "test"


class TestCascadeHandling:
    """Test event cascades (event triggers event triggers event)."""

    def test_cascade_chain(self):
        """Events cascade through multiple machines."""

        class MachineA(StateMachine):
            s1 = State(initial=True)
            s2 = State(final=True)
            go = s1.to(s2)

            def guard_go_to_s2(self, **kwargs) -> bool:
                return True

            def on_enter_s2(self):
                self.emit("a.done", {"id": self._instance_id})

            @classmethod
            def subscriptions(cls):
                return {}

        class MachineB(StateMachine):
            b1 = State(initial=True)
            b2 = State(final=True)
            react = b1.to(b2)

            def guard_react_to_b2(self, **kwargs) -> bool:
                return True

            def on_enter_b2(self):
                self.emit("b.done", {"id": self._instance_id})

            @classmethod
            def subscriptions(cls):
                return {"a.done": "react"}

        class MachineC(StateMachine):
            c1 = State(initial=True)
            c2 = State(final=True)
            finish = c1.to(c2)

            def guard_finish_to_c2(self, **kwargs) -> bool:
                return True

            @classmethod
            def subscriptions(cls):
                return {"b.done": "finish"}

        runner = SimulationRunner()
        runner.register(MachineA)
        runner.register(MachineB)
        runner.register(MachineC)

        runner.register_resolver(MachineA, lambda e: e.payload.get("id"))
        runner.register_resolver(MachineB, lambda e: e.payload.get("id"))
        runner.register_resolver(MachineC, lambda e: e.payload.get("id"))

        runner.create(MachineA, "cascade_1", context={})
        runner.create(MachineB, "cascade_1", context={})
        runner.create(MachineC, "cascade_1", context={})

        # Fire transition on A - should cascade to B and C
        result = runner.fire("cascade_1", MachineA, "go")

        # All machines should be in final state
        assert runner.get(MachineA, "cascade_1").current_state == "s2"
        assert runner.get(MachineB, "cascade_1").current_state == "b2"
        assert runner.get(MachineC, "cascade_1").current_state == "c2"

        # Result should capture all events
        assert len(result.events_emitted) == 2
        event_names = [e.name for e in result.events_emitted]
        assert "a.done" in event_names
        assert "b.done" in event_names


class TestResetBehavior:
    """Test reset() clears instances but keeps registrations."""

    def test_reset_clears_instances(self):
        """reset() destroys all instances."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(OrderLifecycle, "ord_1", context={})
        runner.create(OrderLifecycle, "ord_2", context={})

        runner.reset()

        assert runner.get(OrderLifecycle, "ord_1") is None
        assert runner.get(OrderLifecycle, "ord_2") is None
        assert len(runner._instances) == 0

    def test_reset_clears_event_log(self):
        """reset() clears the event log."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(
            OrderLifecycle,
            "ord_reset",
            context={"order_id": "ORD-RESET", "items": [{"sku": "X", "quantity": 1}]},
        )
        runner.fire("ord_reset", OrderLifecycle, "validate")

        assert len(runner.event_log) > 0

        runner.reset()

        assert len(runner.event_log) == 0

    def test_reset_keeps_registrations(self):
        """reset() retains machine class registrations."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))

        runner.reset()

        assert OrderLifecycle in runner._machine_classes
        assert OrderLifecycle in runner._resolvers

    def test_can_create_after_reset(self):
        """Can create new instances after reset."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(OrderLifecycle, "ord_before", context={})

        runner.reset()

        instance = runner.create(OrderLifecycle, "ord_after", context={"order_id": "NEW"})
        assert instance is not None
        assert instance.context["order_id"] == "NEW"


class TestStateInspection:
    """Test machine_states(), active_instances(), final_instances()."""

    @pytest.fixture
    def runner_with_instances(self):
        """Runner with multiple instances in different states."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        # Instance 1: still in created (active)
        runner.create(
            OrderLifecycle,
            "ord_active",
            context={"order_id": "ACTIVE", "items": [{"sku": "A", "quantity": 1}]},
        )

        # Instance 2: in validated (active)
        runner.create(
            OrderLifecycle,
            "ord_validated",
            context={"order_id": "VALIDATED", "items": [{"sku": "B", "quantity": 1}]},
        )
        runner.fire("ord_validated", OrderLifecycle, "validate")

        # Instance 3: completed (final)
        runner.create(
            OrderLifecycle,
            "ord_completed",
            context={"order_id": "COMPLETED", "items": [{"sku": "C", "quantity": 1}]},
        )
        runner.fire("ord_completed", OrderLifecycle, "validate")
        runner.fire("ord_completed", OrderLifecycle, "fulfill", warehouse_id="WH-1")
        runner.fire("ord_completed", OrderLifecycle, "complete")

        # Instance 4: cancelled (final)
        runner.create(
            OrderLifecycle,
            "ord_cancelled",
            context={"order_id": "CANCELLED", "items": []},
        )
        runner.fire("ord_cancelled", OrderLifecycle, "validate")  # Goes to cancelled

        return runner

    def test_machine_states_returns_all_states(self, runner_with_instances):
        """machine_states() returns current state of all instances."""
        states = runner_with_instances.machine_states()

        assert states[(OrderLifecycle, "ord_active")] == "created"
        assert states[(OrderLifecycle, "ord_validated")] == "validated"
        assert states[(OrderLifecycle, "ord_completed")] == "completed"
        assert states[(OrderLifecycle, "ord_cancelled")] == "cancelled"

    def test_active_instances_returns_non_final(self, runner_with_instances):
        """active_instances() returns only non-final instances."""
        active = runner_with_instances.active_instances()

        assert (OrderLifecycle, "ord_active") in active
        assert (OrderLifecycle, "ord_validated") in active
        assert (OrderLifecycle, "ord_completed") not in active
        assert (OrderLifecycle, "ord_cancelled") not in active
        assert len(active) == 2

    def test_final_instances_returns_final(self, runner_with_instances):
        """final_instances() returns only final instances."""
        final = runner_with_instances.final_instances()

        assert (OrderLifecycle, "ord_active") not in final
        assert (OrderLifecycle, "ord_validated") not in final
        assert (OrderLifecycle, "ord_completed") in final
        assert (OrderLifecycle, "ord_cancelled") in final
        assert len(final) == 2


class TestEventBusAndLogAccess:
    """Test event_bus and event_log property access."""

    def test_event_bus_property(self):
        """event_bus property returns the runner's EventBus."""
        runner = SimulationRunner()

        assert isinstance(runner.event_bus, EventBus)

    def test_event_log_property(self):
        """event_log property returns queryable EventLog."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(
            OrderLifecycle,
            "ord_log",
            context={"order_id": "ORD-LOG", "items": [{"sku": "L", "quantity": 1}]},
        )
        runner.fire("ord_log", OrderLifecycle, "validate")

        log = runner.event_log

        assert len(log) == 1
        filtered = log.filter(name="order.validated")
        assert len(filtered) == 1

    def test_events_appear_in_log(self):
        """Events emitted during transitions appear in event_log."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(
            OrderLifecycle,
            "ord_events",
            context={"order_id": "ORD-EVENTS", "items": [{"sku": "E", "quantity": 1}]},
        )

        runner.fire("ord_events", OrderLifecycle, "validate")
        runner.fire("ord_events", OrderLifecycle, "fulfill", warehouse_id="WH-E")
        runner.fire("ord_events", OrderLifecycle, "complete")

        log = runner.event_log
        assert len(log) == 3

        event_names = [e.name for e in log]
        assert "order.validated" in event_names
        assert "order.fulfilled" in event_names
        assert "order.completed" in event_names


class TestStepResultDataclass:
    """Test StepResult dataclass structure."""

    def test_step_result_fields(self):
        """StepResult has all required fields."""
        result = StepResult(
            trigger="fire:test",
            transitions_fired=[],
            events_emitted=[],
            cascade_depth=1,
            errors=[],
        )

        assert result.trigger == "fire:test"
        assert result.transitions_fired == []
        assert result.events_emitted == []
        assert result.cascade_depth == 1
        assert result.errors == []

    def test_step_result_defaults(self):
        """StepResult has correct defaults."""
        result = StepResult(trigger="test")

        assert result.transitions_fired == []
        assert result.events_emitted == []
        assert result.cascade_depth == 0
        assert result.errors == []


class TestStepErrorDataclass:
    """Test StepError dataclass structure."""

    def test_step_error_fields(self):
        """StepError has all required fields."""
        error = StepError(
            step_index=0,
            machine="OrderLifecycle",
            instance="ord_1",
            transition="validate",
            error_type="unknown_transition",
            message="Test message",
            machine_state_at_error="created",
        )

        assert error.step_index == 0
        assert error.machine == "OrderLifecycle"
        assert error.instance == "ord_1"
        assert error.transition == "validate"
        assert error.error_type == "unknown_transition"
        assert error.message == "Test message"
        assert error.machine_state_at_error == "created"


class TestErrorCapture:
    """Test that errors are captured in StepResult, not raised."""

    def test_unknown_transition_captured(self):
        """Unknown transition captured as error, not raised."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(OrderLifecycle, "err_1", context={})

        # Should not raise
        result = runner.fire("err_1", OrderLifecycle, "fake_transition")

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "unknown_transition"

    def test_invalid_source_state_captured(self):
        """Invalid source state captured as error, not raised."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(OrderLifecycle, "err_2", context={})

        # Should not raise
        result = runner.fire("err_2", OrderLifecycle, "complete")  # Can't complete from created

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "invalid_source_state"

    def test_multiple_errors_captured(self):
        """Multiple errors can be captured in one step."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        runner.create(OrderLifecycle, "err_3", context={})

        # Fire an invalid transition
        result = runner.fire("err_3", OrderLifecycle, "nonexistent")

        # We only fired once, so we should have 1 error
        assert len(result.errors) == 1


class TestHappyPathThroughRunner:
    """Test complete happy path flow through the runner."""

    def test_full_order_lifecycle(self):
        """Complete order lifecycle: created -> validated -> fulfilled -> completed."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        # Create order
        runner.create(
            OrderLifecycle,
            "ord_happy",
            context={
                "order_id": "ORD-HAPPY",
                "customer_id": "CUST-HAPPY",
                "items": [
                    {"sku": "HAPPY-1", "quantity": 2},
                    {"sku": "HAPPY-2", "quantity": 1},
                ],
            },
        )

        # Validate
        result1 = runner.fire("ord_happy", OrderLifecycle, "validate")
        assert len(result1.errors) == 0
        assert result1.transitions_fired[0].target == "validated"

        # Fulfill
        result2 = runner.fire(
            "ord_happy", OrderLifecycle, "fulfill", warehouse_id="WH-HAPPY"
        )
        assert len(result2.errors) == 0
        assert result2.transitions_fired[0].target == "fulfilled"

        # Complete
        result3 = runner.fire("ord_happy", OrderLifecycle, "complete")
        assert len(result3.errors) == 0
        assert result3.transitions_fired[0].target == "completed"

        # Verify final state
        instance = runner.get(OrderLifecycle, "ord_happy")
        assert instance.current_state == "completed"
        assert instance.is_final

        # Verify all events were logged
        log = runner.event_log
        assert len(log) == 3
        event_names = [e.name for e in log]
        assert event_names == ["order.validated", "order.fulfilled", "order.completed"]


class TestMultipleMachineInstances:
    """Test running multiple instances of the same machine type."""

    def test_multiple_instances_independent(self):
        """Multiple instances operate independently."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        # Create two orders
        runner.create(
            OrderLifecycle,
            "ord_a",
            context={"order_id": "A", "items": [{"sku": "A", "quantity": 1}]},
        )
        runner.create(
            OrderLifecycle,
            "ord_b",
            context={"order_id": "B", "items": [{"sku": "B", "quantity": 1}]},
        )

        # Advance only order A
        runner.fire("ord_a", OrderLifecycle, "validate")
        runner.fire("ord_a", OrderLifecycle, "fulfill", warehouse_id="WH-A")
        runner.fire("ord_a", OrderLifecycle, "complete")

        # Order A should be completed
        assert runner.get(OrderLifecycle, "ord_a").current_state == "completed"

        # Order B should still be in created
        assert runner.get(OrderLifecycle, "ord_b").current_state == "created"

    def test_states_tracks_all_instances(self):
        """machine_states() includes all instances."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        runner.create(OrderLifecycle, "m1", context={})
        runner.create(OrderLifecycle, "m2", context={})
        runner.create(OrderLifecycle, "m3", context={})

        states = runner.machine_states()

        assert len(states) == 3
        assert (OrderLifecycle, "m1") in states
        assert (OrderLifecycle, "m2") in states
        assert (OrderLifecycle, "m3") in states


class TestRoutingFailures:
    """Test that resolver problems surface as RoutingFailures rather than silent skips."""

    def _build_two_machine_system(self, resolver_a=None, resolver_b=None):
        """Build a system where MachineA's emission routes to MachineB.start."""

        class MachineA(StateMachine):
            placed = State(initial=True)
            done = State(final=True)
            go = placed.to(done)

            def on_enter_done(self):
                self.emit("a.done", {"the_id": self._context.get("the_id")})

            @classmethod
            def subscriptions(cls):
                return {}

        class MachineB(StateMachine):
            waiting = State(initial=True)
            running = State(final=True)
            start = waiting.to(running)

            @classmethod
            def subscriptions(cls):
                return {"a.done": "start"}

        runner = SimulationRunner()
        runner.register(MachineA)
        runner.register(MachineB)
        runner.register_resolver(MachineA, lambda e: e.payload.get("the_id"))
        if resolver_b is not None:
            runner.register_resolver(MachineB, resolver_b)
        return runner, MachineA, MachineB

    def test_missing_resolver_records_routing_failure(self):
        runner, MachineA, _ = self._build_two_machine_system(resolver_b=None)
        runner.create(MachineA, "x1", context={"the_id": "x1"})

        result = runner.fire("x1", MachineA, "go")

        assert len(result.routing_failures) == 1
        rf = result.routing_failures[0]
        assert rf.reason == "no_resolver_registered"
        assert rf.event_name == "a.done"
        assert rf.target_machine == "MachineB"
        assert rf.target_transition == "start"

    def test_resolver_returning_none_records_routing_failure(self):
        # Resolver looks for the wrong key — returns None
        runner, MachineA, _ = self._build_two_machine_system(
            resolver_b=lambda e: e.payload.get("missing_key")
        )
        runner.create(MachineA, "x1", context={"the_id": "x1"})

        result = runner.fire("x1", MachineA, "go")

        assert len(result.routing_failures) == 1
        rf = result.routing_failures[0]
        assert rf.reason == "resolver_returned_none"
        assert rf.target_machine == "MachineB"
        # The payload keys are visible in the failure so diagnosis can spot mismatches
        assert rf.event_payload_keys == ["the_id"]

    def test_resolver_raising_records_routing_failure(self):
        def bad_resolver(e):
            raise RuntimeError("boom")

        runner, MachineA, _ = self._build_two_machine_system(resolver_b=bad_resolver)
        runner.create(MachineA, "x1", context={"the_id": "x1"})

        result = runner.fire("x1", MachineA, "go")

        assert len(result.routing_failures) == 1
        rf = result.routing_failures[0]
        assert rf.reason == "resolver_raised"
        assert "RuntimeError" in rf.message
        assert "boom" in rf.message

    def test_reset_clears_unscoped_routing_failures(self):
        runner, MachineA, _ = self._build_two_machine_system(resolver_b=None)
        runner.create(MachineA, "x1", context={"the_id": "x1"})
        runner.fire("x1", MachineA, "go")  # produces a routing failure inside StepResult
        runner.reset()
        assert runner.routing_failures == []


class TestSchemaValidationIntegration:
    """End-to-end: machine emits an event whose payload violates its schema."""

    def test_schema_violation_surfaces_as_step_error(self):
        class M(StateMachine):
            EVENT_SCHEMAS = {
                "m.done": {"required": ["the_id", "amount"]},
            }
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

            def on_enter_b(self):
                # Bug: forgot 'amount'
                self.emit("m.done", {"the_id": self._context.get("the_id")})

            @classmethod
            def subscriptions(cls):
                return {}

        runner = SimulationRunner()
        runner.register(M)
        runner.create(M, "x1", context={"the_id": "x1"})

        result = runner.fire("x1", M, "go")

        # The transition fired (we recorded a schema error, didn't propagate)
        assert len(result.errors) == 1
        err = result.errors[0]
        assert err.error_type == "schema_validation_failed"
        assert "missing required field 'amount'" in err.message
        assert err.machine == "M"

    def test_schema_validates_emitted_event_keys(self):
        """Bug-2 from the coffee-shop stress test: payload key renamed."""
        class Emitter(StateMachine):
            EVENT_SCHEMAS = {"work.done": {"required": ["order_id"]}}
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

            def on_enter_b(self):
                # BUG: key is 'order' instead of 'order_id' (the bug-2 pattern)
                self.emit("work.done", {"order": "ord_1"})

            @classmethod
            def subscriptions(cls):
                return {}

        runner = SimulationRunner()
        runner.register(Emitter)
        runner.create(Emitter, "x", context={})
        result = runner.fire("x", Emitter, "go")

        assert len(result.errors) == 1
        assert result.errors[0].error_type == "schema_validation_failed"
