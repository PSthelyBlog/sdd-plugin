"""Tests for the invariants module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sdd.events import Event, EventLog
from sdd.invariants import (
    InvariantReport,
    InvariantResult,
    check_invariant,
    check_invariants,
    load_invariants,
    load_invariants_from_module,
)


# =============================================================================
# InvariantResult Tests
# =============================================================================


class TestInvariantResult:
    """Tests for InvariantResult dataclass."""

    def test_passed_result(self):
        """A passing result has passed=True and no violation details."""
        result = InvariantResult(
            passed=True,
            invariant_name="test_invariant",
        )
        assert result.passed is True
        assert result.invariant_name == "test_invariant"
        assert result.violation_details is None

    def test_failed_result(self):
        """A failing result has passed=False and violation details."""
        result = InvariantResult(
            passed=False,
            invariant_name="test_invariant",
            violation_details="Something went wrong",
        )
        assert result.passed is False
        assert result.invariant_name == "test_invariant"
        assert result.violation_details == "Something went wrong"


# =============================================================================
# InvariantReport Tests
# =============================================================================


class TestInvariantReport:
    """Tests for InvariantReport dataclass."""

    def test_empty_report(self):
        """An empty report has all zeros and all_passed is True."""
        report = InvariantReport()
        assert report.total_checked == 0
        assert report.total_passed == 0
        assert report.total_failed == 0
        assert report.all_passed is True
        assert report.failures == []

    def test_all_passing_report(self):
        """A report with all passing results."""
        report = InvariantReport(
            results=[
                InvariantResult(passed=True, invariant_name="inv1"),
                InvariantResult(passed=True, invariant_name="inv2"),
            ],
            total_checked=2,
            total_passed=2,
            total_failed=0,
        )
        assert report.all_passed is True
        assert report.failures == []

    def test_report_with_failures(self):
        """A report with some failures."""
        failing_result = InvariantResult(
            passed=False,
            invariant_name="inv2",
            violation_details="Failed",
        )
        report = InvariantReport(
            results=[
                InvariantResult(passed=True, invariant_name="inv1"),
                failing_result,
            ],
            total_checked=2,
            total_passed=1,
            total_failed=1,
        )
        assert report.all_passed is False
        assert report.failures == [failing_result]

    def test_failures_property(self):
        """The failures property returns only failed results."""
        fail1 = InvariantResult(passed=False, invariant_name="fail1", violation_details="F1")
        fail2 = InvariantResult(passed=False, invariant_name="fail2", violation_details="F2")
        report = InvariantReport(
            results=[
                InvariantResult(passed=True, invariant_name="pass1"),
                fail1,
                InvariantResult(passed=True, invariant_name="pass2"),
                fail2,
            ],
            total_checked=4,
            total_passed=2,
            total_failed=2,
        )
        assert report.failures == [fail1, fail2]


# =============================================================================
# check_invariant Tests
# =============================================================================


def _make_event_log(events: list[Event] | None = None) -> EventLog:
    """Helper to create an EventLog from events."""
    return EventLog(events or [])


class TestCheckInvariant:
    """Tests for check_invariant function."""

    def test_passing_invariant(self):
        """An invariant that passes returns a passing result."""

        def invariant_always_passes(log, states):
            pass

        result = check_invariant(invariant_always_passes, _make_event_log(), {})
        assert result.passed is True
        assert result.invariant_name == "invariant_always_passes"
        assert result.violation_details is None

    def test_failing_invariant(self):
        """An invariant that raises AssertionError returns a failing result."""

        def invariant_always_fails(log, states):
            assert False, "This invariant always fails"

        result = check_invariant(invariant_always_fails, _make_event_log(), {})
        assert result.passed is False
        assert result.invariant_name == "invariant_always_fails"
        # Violation details should contain the message (may also contain assertion text)
        assert "This invariant always fails" in result.violation_details

    def test_failing_invariant_no_message(self):
        """An invariant that raises AssertionError without message."""

        def invariant_fails_no_message(log, states):
            assert False

        result = check_invariant(invariant_fails_no_message, _make_event_log(), {})
        assert result.passed is False
        assert result.invariant_name == "invariant_fails_no_message"
        # Without a message, we get a default or the assertion text
        assert result.violation_details is not None
        assert len(result.violation_details) > 0

    def test_invariant_can_access_log(self):
        """An invariant receives the event log correctly."""
        events = [
            Event(
                name="test.event",
                payload={"value": 42},
                source_machine="TestMachine",
                source_instance="test_1",
            )
        ]
        log = _make_event_log(events)
        received_log = None

        def invariant_checks_log(log, states):
            nonlocal received_log
            received_log = log
            assert len(log) == 1
            assert log[0].name == "test.event"

        result = check_invariant(invariant_checks_log, log, {})
        assert result.passed is True
        assert received_log is not None

    def test_invariant_can_access_states(self):
        """An invariant receives the states dict correctly."""
        states = {
            ("OrderLifecycle", "ord_1"): "completed",
            ("PaymentFlow", "ord_1"): "captured",
        }
        received_states = None

        def invariant_checks_states(log, states):
            nonlocal received_states
            received_states = states
            assert ("OrderLifecycle", "ord_1") in states
            assert states[("OrderLifecycle", "ord_1")] == "completed"

        result = check_invariant(invariant_checks_states, _make_event_log(), states)
        assert result.passed is True
        assert received_states == states


# =============================================================================
# check_invariants Tests
# =============================================================================


class TestCheckInvariants:
    """Tests for check_invariants function."""

    def test_empty_list(self):
        """Checking an empty list of invariants returns an empty report."""
        report = check_invariants([], _make_event_log(), {})
        assert report.total_checked == 0
        assert report.total_passed == 0
        assert report.total_failed == 0
        assert report.all_passed is True

    def test_all_passing(self):
        """All invariants passing."""

        def inv1(log, states):
            pass

        def inv2(log, states):
            pass

        report = check_invariants([inv1, inv2], _make_event_log(), {})
        assert report.total_checked == 2
        assert report.total_passed == 2
        assert report.total_failed == 0
        assert report.all_passed is True
        assert len(report.results) == 2
        assert all(r.passed for r in report.results)

    def test_some_failing(self):
        """Some invariants failing."""

        def inv_pass(log, states):
            pass

        def inv_fail(log, states):
            assert False, "Failed"

        report = check_invariants([inv_pass, inv_fail], _make_event_log(), {})
        assert report.total_checked == 2
        assert report.total_passed == 1
        assert report.total_failed == 1
        assert report.all_passed is False
        assert report.results[0].passed is True
        assert report.results[1].passed is False

    def test_all_failing(self):
        """All invariants failing."""

        def inv_fail1(log, states):
            assert False, "Failed 1"

        def inv_fail2(log, states):
            assert False, "Failed 2"

        report = check_invariants([inv_fail1, inv_fail2], _make_event_log(), {})
        assert report.total_checked == 2
        assert report.total_passed == 0
        assert report.total_failed == 2
        assert report.all_passed is False


# =============================================================================
# load_invariants Tests
# =============================================================================


class TestLoadInvariants:
    """Tests for load_invariants function."""

    def test_load_from_directory(self):
        """Load invariants from a directory with Python files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invariant file
            invariant_file = Path(tmpdir) / "test_invariants.py"
            invariant_file.write_text(
                """
def invariant_test_one(log, states):
    '''First test invariant.'''
    pass

def invariant_test_two(log, states):
    '''Second test invariant.'''
    pass

def not_an_invariant(log, states):
    '''This should not be loaded.'''
    pass
"""
            )

            invariants = load_invariants(tmpdir)
            assert len(invariants) == 2
            names = {inv.__name__ for inv in invariants}
            assert names == {"invariant_test_one", "invariant_test_two"}

    def test_load_from_subdirectories(self):
        """Load invariants from subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create subdirectory
            subdir = Path(tmpdir) / "order"
            subdir.mkdir()

            # Create invariant file in subdirectory
            invariant_file = subdir / "order_invariants.py"
            invariant_file.write_text(
                """
def invariant_order_valid(log, states):
    pass
"""
            )

            invariants = load_invariants(tmpdir)
            assert len(invariants) == 1
            assert invariants[0].__name__ == "invariant_order_valid"

    def test_nonexistent_directory(self):
        """Raise FileNotFoundError for nonexistent directory."""
        with pytest.raises(FileNotFoundError):
            load_invariants("/nonexistent/directory")

    def test_not_a_directory(self):
        """Raise NotADirectoryError if path is not a directory."""
        with tempfile.NamedTemporaryFile() as tmp:
            with pytest.raises(NotADirectoryError):
                load_invariants(tmp.name)

    def test_skip_init_files(self):
        """Skip __init__.py and other dunder files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create __init__.py
            init_file = Path(tmpdir) / "__init__.py"
            init_file.write_text(
                """
def invariant_in_init(log, states):
    '''Should not be loaded.'''
    pass
"""
            )

            # Create regular invariant file
            invariant_file = Path(tmpdir) / "invariants.py"
            invariant_file.write_text(
                """
def invariant_regular(log, states):
    pass
"""
            )

            invariants = load_invariants(tmpdir)
            assert len(invariants) == 1
            assert invariants[0].__name__ == "invariant_regular"

    def test_skip_files_with_errors(self):
        """Skip files that cannot be loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file with syntax errors
            bad_file = Path(tmpdir) / "bad_invariants.py"
            bad_file.write_text("def broken(: syntax error")

            # Create a good file
            good_file = Path(tmpdir) / "good_invariants.py"
            good_file.write_text(
                """
def invariant_good(log, states):
    pass
"""
            )

            invariants = load_invariants(tmpdir)
            assert len(invariants) == 1
            assert invariants[0].__name__ == "invariant_good"

    def test_empty_directory(self):
        """Empty directory returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            invariants = load_invariants(tmpdir)
            assert invariants == []


# =============================================================================
# load_invariants_from_module Tests
# =============================================================================


class TestLoadInvariantsFromModule:
    """Tests for load_invariants_from_module function."""

    def test_load_from_module(self):
        """Load invariants from a module object."""
        import types

        # Define proper named functions
        def invariant_one(log, states):
            pass

        def invariant_two(log, states):
            pass

        def not_an_invariant(log, states):
            pass

        module = types.ModuleType("test_module")
        module.invariant_one = invariant_one
        module.invariant_two = invariant_two
        module.not_an_invariant = not_an_invariant

        invariants = load_invariants_from_module(module)
        assert len(invariants) == 2
        names = {inv.__name__ for inv in invariants}
        assert names == {"invariant_one", "invariant_two"}


# =============================================================================
# Integration Tests with OrderLifecycle
# =============================================================================


class TestInvariantsWithOrderLifecycle:
    """Integration tests using OrderLifecycle and reference invariants."""

    def test_load_order_invariants(self):
        """Load invariants from the invariants directory."""
        from pathlib import Path

        invariants_dir = Path(__file__).parent.parent / "invariants"
        if invariants_dir.exists():
            invariants = load_invariants(invariants_dir)
            # Should have the order invariants defined
            assert len(invariants) > 0
            names = {inv.__name__ for inv in invariants}
            assert "invariant_completed_orders_have_validated_event" in names

    def test_invariant_passes_for_happy_path(self):
        """Invariants pass for a normal order flow."""
        from machines.order_lifecycle import OrderLifecycle
        from sdd.events import EventBus

        # Create and run an order through the happy path
        bus = EventBus()
        order = OrderLifecycle(
            context={
                "order_id": "ord_1",
                "customer_id": "cust_1",
                "items": [{"sku": "WIDGET", "quantity": 2}],
            },
            event_bus=bus,
            instance_id="ord_1",
        )

        # Run through happy path
        order.fire("validate")
        order.fire("fulfill", warehouse_id="WH_EAST")
        order.fire("complete")

        # Get states and log
        states = {("OrderLifecycle", "ord_1"): order.current_state}
        log = bus.log

        # Load invariants and check
        invariants_dir = Path(__file__).parent.parent / "invariants"
        if invariants_dir.exists():
            invariants = load_invariants(invariants_dir)
            report = check_invariants(invariants, log, states)
            assert report.all_passed, f"Failures: {[f.violation_details for f in report.failures]}"

    def test_invariant_fails_for_missing_validation(self):
        """Invariants fail when order completes without validation."""
        from sdd.events import Event

        # Simulate an order that completed without validation
        # (This shouldn't happen with the real machine, but let's test the invariant)
        events = [
            Event(
                name="order.fulfilled",
                payload={"order_id": "ord_1", "warehouse_id": "WH_EAST"},
                source_machine="OrderLifecycle",
                source_instance="ord_1",
            ),
            Event(
                name="order.completed",
                payload={"order_id": "ord_1"},
                source_machine="OrderLifecycle",
                source_instance="ord_1",
            ),
        ]
        log = EventLog(events)
        states = {("OrderLifecycle", "ord_1"): "completed"}

        # Check the specific invariant
        from invariants.order_invariants import invariant_completed_orders_have_validated_event

        result = check_invariant(
            invariant_completed_orders_have_validated_event, log, states
        )
        assert result.passed is False
        assert "without validation" in result.violation_details


# =============================================================================
# ScenarioRunner Integration Tests
# =============================================================================


class TestScenarioRunnerWithInvariants:
    """Tests for ScenarioRunner with invariant checking."""

    def test_scenario_with_passing_invariants(self):
        """Scenario passes when all invariants pass."""
        from machines.order_lifecycle import OrderLifecycle
        from sdd.runner import SimulationRunner
        from sdd.scenario import CreateStep, FireStep, Scenario, ScenarioRunner

        # Set up runner
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        scenario_runner = ScenarioRunner(runner)

        # Create scenario
        scenario = Scenario(
            name="happy_path",
            narrative="Order completes successfully",
            steps=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "customer_id": "cust_1",
                        "items": [{"sku": "WIDGET", "quantity": 2}],
                    },
                ),
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",
                    args={"warehouse_id": "WH_EAST"},
                ),
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="complete"),
            ],
        )

        # Define simple invariant
        def invariant_completed_has_events(log, states):
            for (machine, instance), state in states.items():
                if machine == "OrderLifecycle" and state == "completed":
                    events = list(log.filter(source_instance=instance))
                    assert len(events) > 0

        result = scenario_runner.run(scenario, invariants=[invariant_completed_has_events])
        assert result.passed is True
        assert result.invariant_results is not None
        assert result.invariant_results.all_passed is True

    def test_scenario_with_failing_invariants(self):
        """Scenario fails when an invariant fails."""
        from machines.order_lifecycle import OrderLifecycle
        from sdd.runner import SimulationRunner
        from sdd.scenario import CreateStep, FireStep, Scenario, ScenarioRunner

        # Set up runner
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        scenario_runner = ScenarioRunner(runner)

        # Create scenario
        scenario = Scenario(
            name="happy_path",
            narrative="Order completes successfully",
            steps=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "customer_id": "cust_1",
                        "items": [{"sku": "WIDGET", "quantity": 2}],
                    },
                ),
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",
                    args={"warehouse_id": "WH_EAST"},
                ),
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="complete"),
            ],
        )

        # Define failing invariant
        def invariant_always_fails(log, states):
            assert False, "This invariant always fails"

        result = scenario_runner.run(scenario, invariants=[invariant_always_fails])
        assert result.passed is False
        assert result.invariant_results is not None
        assert result.invariant_results.all_passed is False
        assert "always fails" in result.failure_reason

    def test_run_all_with_invariants(self):
        """Run multiple scenarios with invariants."""
        from machines.order_lifecycle import OrderLifecycle
        from sdd.runner import SimulationRunner
        from sdd.scenario import CreateStep, FireStep, Scenario, ScenarioRunner

        # Set up runner
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        scenario_runner = ScenarioRunner(runner)

        # Create scenarios
        scenarios = [
            Scenario(
                name="scenario_1",
                narrative="First scenario",
                steps=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_1",
                        context={
                            "order_id": "ord_1",
                            "customer_id": "cust_1",
                            "items": [{"sku": "WIDGET", "quantity": 2}],
                        },
                    ),
                    FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                ],
            ),
            Scenario(
                name="scenario_2",
                narrative="Second scenario",
                steps=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_2",
                        context={
                            "order_id": "ord_2",
                            "customer_id": "cust_2",
                            "items": [{"sku": "GADGET", "quantity": 1}],
                        },
                    ),
                    FireStep(machine="OrderLifecycle", instance_id="ord_2", transition="validate"),
                ],
            ),
        ]

        def invariant_simple(log, states):
            pass

        results = scenario_runner.run_all(scenarios, invariants=[invariant_simple])
        assert len(results) == 2
        assert all(r.passed for r in results)
        assert all(r.invariant_results is not None for r in results)

    def test_scenario_failure_before_invariants(self):
        """Invariants are still checked even when scenario fails earlier."""
        from machines.order_lifecycle import OrderLifecycle
        from sdd.runner import SimulationRunner
        from sdd.scenario import CreateStep, FireStep, Scenario, ScenarioRunner

        # Set up runner
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        scenario_runner = ScenarioRunner(runner)

        # Create scenario that will fail
        scenario = Scenario(
            name="failing_scenario",
            narrative="This scenario will fail",
            steps=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "customer_id": "cust_1",
                        "items": [{"sku": "WIDGET", "quantity": 2}],
                    },
                ),
                # Try to complete before fulfilling - should fail
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="complete"),
            ],
        )

        def invariant_always_passes(log, states):
            pass

        result = scenario_runner.run(scenario, invariants=[invariant_always_passes])
        # Scenario should fail due to invalid transition, invariants are still checked
        assert result.passed is False
        assert result.invariant_results is not None
