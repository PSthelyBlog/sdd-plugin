"""Tests for sdd.events module."""

import time
import pytest
from sdd.events import Event, EventBus, EventLog, SchemaValidationError, validate_payload


class TestEvent:
    """Tests for Event dataclass."""

    def test_event_creation(self):
        """Can create an Event with required fields."""
        event = Event(
            name="order.validated",
            payload={"order_id": "123"},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        assert event.name == "order.validated"
        assert event.payload["order_id"] == "123"
        assert event.source_machine == "OrderLifecycle"
        assert event.source_instance == "order_123"

    def test_event_is_frozen(self):
        """Event is immutable."""
        event = Event(
            name="order.validated",
            payload={"order_id": "123"},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        with pytest.raises(AttributeError):
            event.name = "changed"

    def test_event_has_timestamp(self):
        """Event gets a timestamp automatically."""
        before = time.monotonic()
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        after = time.monotonic()
        assert before <= event.timestamp <= after

    def test_event_default_correlation_id(self):
        """Event has empty correlation_id by default."""
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        assert event.correlation_id == ""

    def test_event_custom_correlation_id(self):
        """Event can have custom correlation_id."""
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
            correlation_id="corr_abc123",
        )
        assert event.correlation_id == "corr_abc123"

    def test_payload_copied(self):
        """Payload is copied to prevent external mutation."""
        original = {"order_id": "123"}
        event = Event(
            name="order.validated",
            payload=original,
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        original["order_id"] = "456"
        assert event.payload["order_id"] == "123"


class TestEventBus:
    """Tests for EventBus class."""

    def test_emit_adds_to_log(self):
        """emit() adds event to the log."""
        bus = EventBus()
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        bus.emit(event)
        assert len(bus.log) == 1
        assert bus.log[0].name == "order.validated"

    def test_log_is_readonly_copy(self):
        """log property returns a copy."""
        bus = EventBus()
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        bus.emit(event)
        log = bus.log
        assert isinstance(log, EventLog)
        assert len(log) == 1

    def test_subscribe_receives_events(self):
        """Subscribed handler receives matching events."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe("order.validated", handler)
        event = Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        )
        bus.emit(event)
        assert len(received) == 1
        assert received[0].name == "order.validated"

    def test_subscribe_filters_by_name(self):
        """Handler only receives events with matching name."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe("order.validated", handler)
        bus.emit(Event(
            name="order.cancelled",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        ))
        assert len(received) == 0

    def test_subscribe_pattern_glob(self):
        """Pattern subscription uses glob matching."""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe_pattern("order.*", handler)
        bus.emit(Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        ))
        bus.emit(Event(
            name="order.cancelled",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        ))
        bus.emit(Event(
            name="payment.authorized",
            payload={},
            source_machine="PaymentFlow",
            source_instance="payment_123",
        ))
        assert len(received) == 2
        assert all(e.name.startswith("order.") for e in received)

    def test_multiple_subscribers(self):
        """Multiple handlers can subscribe to same event."""
        bus = EventBus()
        received1 = []
        received2 = []

        bus.subscribe("order.validated", lambda e: received1.append(e))
        bus.subscribe("order.validated", lambda e: received2.append(e))

        bus.emit(Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        ))
        assert len(received1) == 1
        assert len(received2) == 1

    def test_cascade_events(self):
        """Events emitted by handlers are delivered."""
        bus = EventBus()
        events = []

        def handler1(event):
            events.append(event)
            bus.emit(Event(
                name="order.processed",
                payload={},
                source_machine="Handler",
                source_instance="h1",
            ))

        def handler2(event):
            events.append(event)

        bus.subscribe("order.validated", handler1)
        bus.subscribe("order.processed", handler2)

        bus.emit(Event(
            name="order.validated",
            payload={},
            source_machine="OrderLifecycle",
            source_instance="order_123",
        ))
        assert len(events) == 2
        assert events[0].name == "order.validated"
        assert events[1].name == "order.processed"

    def test_cascade_limit_exceeded(self):
        """Cascade limit prevents infinite loops."""
        bus = EventBus(max_cascade_depth=5)

        def infinite_handler(event):
            bus.emit(Event(
                name="loop",
                payload={},
                source_machine="Looper",
                source_instance="l1",
            ))

        bus.subscribe("loop", infinite_handler)

        with pytest.raises(RuntimeError, match="cascade exceeded"):
            bus.emit(Event(
                name="loop",
                payload={},
                source_machine="Trigger",
                source_instance="t1",
            ))

    def test_clear(self):
        """clear() resets the bus."""
        bus = EventBus()
        bus.subscribe("test", lambda e: None)
        bus.emit(Event(
            name="test",
            payload={},
            source_machine="Test",
            source_instance="t1",
        ))
        bus.clear()
        assert len(bus.log) == 0


class TestEventLog:
    """Tests for EventLog query interface."""

    @pytest.fixture
    def sample_events(self):
        """Create sample events for testing."""
        return [
            Event(
                name="order.validated",
                payload={},
                source_machine="OrderLifecycle",
                source_instance="order_1",
                timestamp=1.0,
                correlation_id="corr_1",
            ),
            Event(
                name="order.cancelled",
                payload={},
                source_machine="OrderLifecycle",
                source_instance="order_2",
                timestamp=2.0,
                correlation_id="corr_2",
            ),
            Event(
                name="payment.authorized",
                payload={},
                source_machine="PaymentFlow",
                source_instance="payment_1",
                timestamp=3.0,
                correlation_id="corr_1",
            ),
        ]

    def test_filter_by_name(self, sample_events):
        """Filter events by name."""
        log = EventLog(sample_events)
        filtered = log.filter(name="order.validated")
        assert len(filtered) == 1
        assert filtered[0].name == "order.validated"

    def test_filter_by_source_machine(self, sample_events):
        """Filter events by source_machine."""
        log = EventLog(sample_events)
        filtered = log.filter(source_machine="OrderLifecycle")
        assert len(filtered) == 2
        assert all(e.source_machine == "OrderLifecycle" for e in filtered)

    def test_filter_by_source_instance(self, sample_events):
        """Filter events by source_instance."""
        log = EventLog(sample_events)
        filtered = log.filter(source_instance="order_1")
        assert len(filtered) == 1
        assert filtered[0].source_instance == "order_1"

    def test_filter_by_correlation_id(self, sample_events):
        """Filter events by correlation_id."""
        log = EventLog(sample_events)
        filtered = log.filter(correlation_id="corr_1")
        assert len(filtered) == 2

    def test_chained_filters(self, sample_events):
        """Filters can be chained."""
        log = EventLog(sample_events)
        filtered = log.filter(source_machine="OrderLifecycle").filter(name="order.validated")
        assert len(filtered) == 1
        assert filtered[0].name == "order.validated"

    def test_after_timestamp(self, sample_events):
        """Filter events after timestamp."""
        log = EventLog(sample_events)
        filtered = log.after(1.5)
        assert len(filtered) == 2
        assert all(e.timestamp > 1.5 for e in filtered)

    def test_before_timestamp(self, sample_events):
        """Filter events before timestamp."""
        log = EventLog(sample_events)
        filtered = log.before(2.5)
        assert len(filtered) == 2
        assert all(e.timestamp < 2.5 for e in filtered)

    def test_unique_instances(self, sample_events):
        """Get unique instance identifiers."""
        log = EventLog(sample_events)
        instances = log.unique_instances("OrderLifecycle")
        assert instances == {"order_1", "order_2"}

    def test_to_list(self, sample_events):
        """to_list() returns a copy."""
        log = EventLog(sample_events)
        lst = log.to_list()
        assert len(lst) == 3
        assert lst is not log._events

    def test_iteration(self, sample_events):
        """EventLog is iterable."""
        log = EventLog(sample_events)
        events = list(log)
        assert len(events) == 3

    def test_indexing(self, sample_events):
        """EventLog supports indexing."""
        log = EventLog(sample_events)
        assert log[0].name == "order.validated"
        assert log[-1].name == "payment.authorized"

    def test_len(self, sample_events):
        """EventLog supports len()."""
        log = EventLog(sample_events)
        assert len(log) == 3


class TestValidatePayload:
    """Unit tests for the validate_payload helper."""

    def test_empty_schema_accepts_anything(self):
        assert validate_payload({"a": 1}, {}) == []

    def test_missing_required_field(self):
        problems = validate_payload({}, {"required": ["x"]})
        assert problems == ["missing required field 'x'"]

    def test_multiple_missing_required(self):
        problems = validate_payload({}, {"required": ["a", "b"]})
        assert len(problems) == 2

    def test_type_mismatch(self):
        problems = validate_payload(
            {"x": 1},
            {"properties": {"x": {"type": "string"}}},
        )
        assert problems == ["field 'x' expected type 'string', got int"]

    def test_unknown_fields_allowed(self):
        assert validate_payload({"x": 1, "y": "z"}, {"required": ["x"]}) == []

    def test_unknown_type_in_schema_skipped(self):
        # Unknown type strings shouldn't crash; they're just ignored
        assert validate_payload({"x": 1}, {"properties": {"x": {"type": "weird"}}}) == []


class TestEventBusSchemaValidation:
    """Tests for EventBus schema enforcement."""

    def test_register_schema_and_pass_validation(self):
        bus = EventBus()
        bus.register_schema("foo.event", {"required": ["id"]})
        bus.emit(Event(name="foo.event", payload={"id": "x"}, source_machine="M", source_instance="i"))
        assert len(bus.log) == 1

    def test_emit_with_missing_required_raises(self):
        bus = EventBus()
        bus.register_schema("foo.event", {"required": ["id"]})
        with pytest.raises(SchemaValidationError) as exc_info:
            bus.emit(Event(name="foo.event", payload={}, source_machine="M", source_instance="i"))
        assert "missing required field 'id'" in str(exc_info.value)
        assert exc_info.value.event_name == "foo.event"
        # Invalid event must not appear in the log
        assert len(bus.log) == 0

    def test_emit_with_wrong_type_raises(self):
        bus = EventBus()
        bus.register_schema(
            "foo.event",
            {"required": ["id"], "properties": {"id": {"type": "string"}}},
        )
        with pytest.raises(SchemaValidationError):
            bus.emit(Event(name="foo.event", payload={"id": 42}, source_machine="M", source_instance="i"))

    def test_emit_without_registered_schema_passes_through(self):
        bus = EventBus()
        bus.emit(Event(name="no.schema", payload={"anything": True}, source_machine="M", source_instance="i"))
        assert len(bus.log) == 1

    def test_conflicting_schema_registration_raises(self):
        bus = EventBus()
        bus.register_schema("e", {"required": ["a"]})
        with pytest.raises(ValueError, match="Conflicting schemas"):
            bus.register_schema("e", {"required": ["b"]})

    def test_identical_schema_registration_idempotent(self):
        bus = EventBus()
        bus.register_schema("e", {"required": ["a"]})
        bus.register_schema("e", {"required": ["a"]})  # OK
        assert bus.schemas["e"] == {"required": ["a"]}

    def test_strict_schemas_false_allows_emission(self):
        bus = EventBus(strict_schemas=False)
        bus.register_schema("foo.event", {"required": ["id"]})
        # Does not raise; just emits
        bus.emit(Event(name="foo.event", payload={}, source_machine="M", source_instance="i"))
        assert len(bus.log) == 1
