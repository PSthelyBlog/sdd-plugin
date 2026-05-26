"""Tests for the scenario parser and runner."""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path

import pytest

from machines.order_lifecycle import OrderLifecycle
from sdd.protocol import State, StateMachine
from sdd.runner import SimulationRunner
from sdd.scenario import (
    AdvanceTimeStep,
    AssertStep,
    CreateStep,
    EmitStep,
    ExpectBlock,
    FireStep,
    Scenario,
    ScenarioParseError,
    ScenarioParser,
    ScenarioResult,
    ScenarioRunner,
    parse_fire_ref,
    parse_instance_ref,
)


# =============================================================================
# Tests for parse_instance_ref
# =============================================================================


class TestParseInstanceRef:
    """Tests for parsing MachineName("id") format."""

    def test_valid_instance_ref(self):
        """Can parse valid instance reference."""
        machine, instance_id = parse_instance_ref('OrderLifecycle("ord_123")')
        assert machine == "OrderLifecycle"
        assert instance_id == "ord_123"

    def test_instance_ref_with_underscore(self):
        """Can parse instance ID with underscores."""
        machine, instance_id = parse_instance_ref('MyMachine("test_instance_1")')
        assert machine == "MyMachine"
        assert instance_id == "test_instance_1"

    def test_instance_ref_with_hyphen(self):
        """Can parse instance ID with hyphens."""
        machine, instance_id = parse_instance_ref('Payment("pay-123-abc")')
        assert machine == "Payment"
        assert instance_id == "pay-123-abc"

    def test_invalid_instance_ref_no_quotes(self):
        """Raises error for missing quotes."""
        with pytest.raises(ScenarioParseError, match="Invalid instance reference"):
            parse_instance_ref("OrderLifecycle(ord_123)")

    def test_invalid_instance_ref_no_parens(self):
        """Raises error for missing parentheses."""
        with pytest.raises(ScenarioParseError, match="Invalid instance reference"):
            parse_instance_ref('OrderLifecycle"ord_123"')

    def test_invalid_instance_ref_empty(self):
        """Raises error for empty string."""
        with pytest.raises(ScenarioParseError, match="Invalid instance reference"):
            parse_instance_ref("")


# =============================================================================
# Tests for parse_fire_ref
# =============================================================================


class TestParseFireRef:
    """Tests for parsing MachineName("id").transition format."""

    def test_valid_fire_ref(self):
        """Can parse valid fire reference."""
        machine, instance_id, transition = parse_fire_ref('OrderLifecycle("ord_123").validate')
        assert machine == "OrderLifecycle"
        assert instance_id == "ord_123"
        assert transition == "validate"

    def test_fire_ref_with_underscore_transition(self):
        """Can parse transition with underscores."""
        machine, instance_id, transition = parse_fire_ref(
            'PaymentFlow("pay_1").retry_payment'
        )
        assert machine == "PaymentFlow"
        assert instance_id == "pay_1"
        assert transition == "retry_payment"

    def test_invalid_fire_ref_no_transition(self):
        """Raises error for missing transition."""
        with pytest.raises(ScenarioParseError, match="Invalid fire reference"):
            parse_fire_ref('OrderLifecycle("ord_123")')

    def test_invalid_fire_ref_no_dot(self):
        """Raises error for missing dot."""
        with pytest.raises(ScenarioParseError, match="Invalid fire reference"):
            parse_fire_ref('OrderLifecycle("ord_123")validate')

    def test_invalid_fire_ref_empty(self):
        """Raises error for empty string."""
        with pytest.raises(ScenarioParseError, match="Invalid fire reference"):
            parse_fire_ref("")


# =============================================================================
# Tests for ScenarioParser
# =============================================================================


class TestScenarioParser:
    """Tests for YAML scenario parsing."""

    def test_parse_minimal_scenario(self):
        """Can parse scenario with only required fields."""
        yaml_content = """
scenario: minimal_test
narrative: "A minimal test scenario"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            assert scenario.name == "minimal_test"
            assert scenario.narrative == "A minimal test scenario"
            assert scenario.setup == []
            assert scenario.steps == []

    def test_parse_scenario_with_create_step(self):
        """Can parse scenario with create step."""
        yaml_content = """
scenario: create_test
narrative: "Test create step"
setup:
  - create: OrderLifecycle("ord_1")
    context:
      order_id: "ord_1"
      customer_id: "cust_1"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            assert len(scenario.setup) == 1
            step = scenario.setup[0]
            assert isinstance(step, CreateStep)
            assert step.machine == "OrderLifecycle"
            assert step.instance_id == "ord_1"
            assert step.context["order_id"] == "ord_1"
            assert step.context["customer_id"] == "cust_1"

    def test_parse_scenario_with_fire_step(self):
        """Can parse scenario with fire step."""
        yaml_content = """
scenario: fire_test
narrative: "Test fire step"
steps:
  - fire: OrderLifecycle("ord_1").validate
    args:
      warehouse_id: "WH_EAST"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            assert len(scenario.steps) == 1
            step = scenario.steps[0]
            assert isinstance(step, FireStep)
            assert step.machine == "OrderLifecycle"
            assert step.instance_id == "ord_1"
            assert step.transition == "validate"
            assert step.args["warehouse_id"] == "WH_EAST"

    def test_parse_scenario_with_expect_failure(self):
        """Can parse fire step with expect_failure."""
        yaml_content = """
scenario: failure_test
narrative: "Test expected failure"
steps:
  - fire: OrderLifecycle("ord_1").validate
    expect_failure: no_guard_passed
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            step = scenario.steps[0]
            assert isinstance(step, FireStep)
            assert step.expect_failure == "no_guard_passed"

    def test_parse_scenario_with_advance_time(self):
        """Can parse scenario with advance_time step."""
        yaml_content = """
scenario: time_test
narrative: "Test time advancement"
steps:
  - advance_time: {days: 7, hours: 12, minutes: 30}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            step = scenario.steps[0]
            assert isinstance(step, AdvanceTimeStep)
            assert step.days == 7
            assert step.hours == 12
            assert step.minutes == 30

    def test_parse_scenario_with_assert(self):
        """Can parse scenario with assert step."""
        yaml_content = """
scenario: assert_test
narrative: "Test assertions"
steps:
  - assert:
      states:
        OrderLifecycle("ord_1"): validated
      events_emitted:
        - order.validated
      events_not_emitted:
        - order.cancelled
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            step = scenario.steps[0]
            assert isinstance(step, AssertStep)
            assert step.states == {'OrderLifecycle("ord_1")': "validated"}
            assert step.events_emitted == ["order.validated"]
            assert step.events_not_emitted == ["order.cancelled"]

    def test_parse_scenario_with_expect_block(self):
        """Can parse scenario with expect block."""
        yaml_content = """
scenario: expect_test
narrative: "Test expect block"
steps: []
expect:
  states:
    OrderLifecycle("ord_1"): completed
  events_emitted:
    - order.validated
    - order.completed
  events_not_emitted:
    - order.cancelled
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)

            expect = scenario.expect
            assert expect.states == {'OrderLifecycle("ord_1")': "completed"}
            assert expect.events_emitted == ["order.validated", "order.completed"]
            assert expect.events_not_emitted == ["order.cancelled"]

    def test_parse_file_not_found(self):
        """Raises error for missing file."""
        parser = ScenarioParser()
        with pytest.raises(ScenarioParseError, match="Scenario file not found"):
            parser.parse("/nonexistent/file.yaml")

    def test_parse_invalid_yaml(self):
        """Raises error for invalid YAML."""
        yaml_content = """
scenario: invalid
this is not valid: yaml: syntax
  - broken
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="Invalid YAML"):
                parser.parse(f.name)

    def test_parse_missing_scenario_name(self):
        """Raises error for missing scenario name."""
        yaml_content = """
narrative: "Missing scenario name"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="Missing required field.*scenario"):
                parser.parse(f.name)

    def test_parse_directory(self):
        """Can parse all YAML files in a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two scenario files
            with open(Path(tmpdir) / "scenario1.yaml", "w") as f:
                f.write('scenario: test1\nnarrative: "Test 1"')
            with open(Path(tmpdir) / "scenario2.yaml", "w") as f:
                f.write('scenario: test2\nnarrative: "Test 2"')

            parser = ScenarioParser()
            scenarios = parser.parse_directory(tmpdir)

            assert len(scenarios) == 2
            names = {s.name for s in scenarios}
            assert names == {"test1", "test2"}

    def test_parse_directory_with_subdirs(self):
        """Can parse YAML files in subdirectories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()

            with open(Path(tmpdir) / "root.yaml", "w") as f:
                f.write('scenario: root\nnarrative: "Root"')
            with open(subdir / "nested.yaml", "w") as f:
                f.write('scenario: nested\nnarrative: "Nested"')

            parser = ScenarioParser()
            scenarios = parser.parse_directory(tmpdir)

            assert len(scenarios) == 2
            names = {s.name for s in scenarios}
            assert names == {"root", "nested"}


# =============================================================================
# Tests for ScenarioRunner
# =============================================================================


class TestScenarioRunner:
    """Tests for scenario execution."""

    @pytest.fixture
    def runner(self):
        """Create a SimulationRunner with OrderLifecycle registered."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        return runner

    @pytest.fixture
    def scenario_runner(self, runner):
        """Create a ScenarioRunner wrapping the SimulationRunner."""
        return ScenarioRunner(runner)

    def test_execute_create_step(self, scenario_runner, runner):
        """Can execute CreateStep."""
        scenario = Scenario(
            name="create_test",
            narrative="Test create",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed
        assert runner.get(OrderLifecycle, "ord_1") is not None
        instance = runner.get(OrderLifecycle, "ord_1")
        assert instance.current_state == "created"

    def test_execute_fire_step_success(self, scenario_runner, runner):
        """Can execute FireStep successfully."""
        scenario = Scenario(
            name="fire_test",
            narrative="Test fire",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="validate",
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed
        instance = runner.get(OrderLifecycle, "ord_1")
        assert instance.current_state == "validated"

    def test_execute_fire_step_with_args(self, scenario_runner, runner):
        """Can pass args to FireStep."""
        scenario = Scenario(
            name="fire_args_test",
            narrative="Test fire with args",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="validate",
                ),
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",
                    args={"warehouse_id": "WH_EAST"},
                ),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed
        instance = runner.get(OrderLifecycle, "ord_1")
        assert instance.current_state == "fulfilled"
        assert instance.context["warehouse_id"] == "WH_EAST"

    def test_execute_fire_step_failure(self, scenario_runner):
        """Records failure when FireStep fails unexpectedly."""
        scenario = Scenario(
            name="failure_test",
            narrative="Test failure",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",  # Can't fulfill from created state
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert result.failure_step == 1  # The fire step (after create in setup)
        assert "invalid_source_state" in result.failure_reason

    def test_execute_fire_step_with_expect_failure_success(self, scenario_runner):
        """expect_failure inverts success condition - passes when failure matches."""
        scenario = Scenario(
            name="expected_failure_test",
            narrative="Test expected failure",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": []},  # Invalid items
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",  # Can't fulfill from created state
                    expect_failure="invalid_source_state",
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_execute_fire_step_with_expect_failure_wrong_type(self, scenario_runner):
        """expect_failure fails when failure type doesn't match."""
        scenario = Scenario(
            name="wrong_failure_type_test",
            narrative="Test wrong failure type",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": []},
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",  # Will fail with invalid_source_state
                    expect_failure="no_guard_passed",  # But we expected a different failure
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "expected failure" in result.failure_reason.lower()
        assert "invalid_source_state" in result.failure_reason

    def test_execute_fire_step_with_expect_failure_but_success(self, scenario_runner):
        """expect_failure fails when transition unexpectedly succeeds."""
        scenario = Scenario(
            name="unexpected_success_test",
            narrative="Test unexpected success",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="validate",
                    expect_failure="no_guard_passed",  # But it will succeed
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "expected failure" in result.failure_reason.lower()
        assert "succeeded" in result.failure_reason.lower()

    def test_execute_advance_time_step_advances_clock(self, scenario_runner, runner):
        """AdvanceTimeStep advances the runner's virtual clock."""
        scenario = Scenario(
            name="time_test",
            narrative="Test time",
            steps=[AdvanceTimeStep(days=7)],
        )
        result = scenario_runner.run(scenario)
        assert result.passed
        assert runner.virtual_clock == 7 * 86400

    def test_execute_assert_step_states_pass(self, scenario_runner, runner):
        """AssertStep passes when state matches."""
        scenario = Scenario(
            name="assert_state_test",
            narrative="Test state assertion",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                AssertStep(states={'OrderLifecycle("ord_1")': "validated"}),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_execute_assert_step_states_fail(self, scenario_runner):
        """AssertStep fails when state doesn't match."""
        scenario = Scenario(
            name="assert_state_fail_test",
            narrative="Test failed state assertion",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                AssertStep(states={'OrderLifecycle("ord_1")': "validated"}),  # Still created
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "State assertion failed" in result.failure_reason

    def test_execute_assert_step_events_emitted(self, scenario_runner):
        """AssertStep can verify events were emitted."""
        scenario = Scenario(
            name="assert_events_test",
            narrative="Test event assertion",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                AssertStep(events_emitted=["order.validated"]),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_execute_assert_step_events_not_emitted(self, scenario_runner):
        """AssertStep can verify events were NOT emitted."""
        scenario = Scenario(
            name="assert_no_events_test",
            narrative="Test no event assertion",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                AssertStep(events_not_emitted=["order.cancelled"]),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_expect_block_states(self, scenario_runner):
        """ExpectBlock verifies final states."""
        scenario = Scenario(
            name="expect_states_test",
            narrative="Test expect states",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(states={'OrderLifecycle("ord_1")': "validated"}),
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_expect_block_states_fail(self, scenario_runner):
        """ExpectBlock fails when final state doesn't match."""
        scenario = Scenario(
            name="expect_states_fail_test",
            narrative="Test expect states failure",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(states={'OrderLifecycle("ord_1")': "completed"}),  # Wrong
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "Expected state" in result.failure_reason

    def test_expect_block_events_emitted(self, scenario_runner):
        """ExpectBlock verifies events were emitted."""
        scenario = Scenario(
            name="expect_events_test",
            narrative="Test expect events",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(events_emitted=["order.validated"]),
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_expect_block_events_not_emitted(self, scenario_runner):
        """ExpectBlock verifies events were NOT emitted."""
        scenario = Scenario(
            name="expect_no_events_test",
            narrative="Test expect no events",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(events_not_emitted=["order.cancelled"]),
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_expect_block_events_not_emitted_fail(self, scenario_runner):
        """ExpectBlock fails when unwanted event was emitted."""
        scenario = Scenario(
            name="expect_no_events_fail_test",
            narrative="Test expect no events failure",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(events_not_emitted=["order.validated"]),  # But it was emitted
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "should not have been" in result.failure_reason

    def test_scenario_result_has_final_states(self, scenario_runner):
        """ScenarioResult contains final states."""
        scenario = Scenario(
            name="final_states_test",
            narrative="Test final states",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
        )

        result = scenario_runner.run(scenario)

        assert 'OrderLifecycle("ord_1")' in result.final_states
        assert result.final_states['OrderLifecycle("ord_1")'] == "validated"

    def test_scenario_result_has_events(self, scenario_runner):
        """ScenarioResult contains emitted events."""
        scenario = Scenario(
            name="events_test",
            narrative="Test events",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={"order_id": "ord_1", "items": [{"sku": "TEST", "quantity": 1}]},
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
        )

        result = scenario_runner.run(scenario)

        event_names = [e.name for e in result.events_emitted]
        assert "order.validated" in event_names

    def test_run_all_scenarios(self, scenario_runner):
        """Can run multiple scenarios."""
        scenarios = [
            Scenario(
                name="scenario_1",
                narrative="First",
                setup=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_1",
                        context={"order_id": "ord_1", "items": [{"sku": "A", "quantity": 1}]},
                    )
                ],
            ),
            Scenario(
                name="scenario_2",
                narrative="Second",
                setup=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_2",
                        context={"order_id": "ord_2", "items": [{"sku": "B", "quantity": 2}]},
                    )
                ],
            ),
        ]

        results = scenario_runner.run_all(scenarios)

        assert len(results) == 2
        assert results[0].scenario_name == "scenario_1"
        assert results[1].scenario_name == "scenario_2"
        assert all(r.passed for r in results)

    def test_run_all_resets_between_scenarios(self, scenario_runner, runner):
        """Runner resets between scenarios."""
        scenarios = [
            Scenario(
                name="scenario_1",
                narrative="First",
                setup=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_1",
                        context={"order_id": "ord_1", "items": [{"sku": "A", "quantity": 1}]},
                    )
                ],
            ),
            Scenario(
                name="scenario_2",
                narrative="Second",
                setup=[
                    CreateStep(
                        machine="OrderLifecycle",
                        instance_id="ord_1",  # Same ID should work - reset happened
                        context={"order_id": "ord_1", "items": [{"sku": "B", "quantity": 2}]},
                    )
                ],
            ),
        ]

        results = scenario_runner.run_all(scenarios)

        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_unknown_machine_class(self, scenario_runner):
        """Fails with helpful message for unknown machine class."""
        scenario = Scenario(
            name="unknown_machine_test",
            narrative="Test unknown machine",
            setup=[
                CreateStep(
                    machine="NonExistentMachine",
                    instance_id="id_1",
                    context={},
                )
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "Unknown machine class" in result.failure_reason
        assert "NonExistentMachine" in result.failure_reason


# =============================================================================
# Tests for full scenario execution with OrderLifecycle
# =============================================================================


class TestFullScenarioExecution:
    """Tests for complete scenario execution with OrderLifecycle."""

    @pytest.fixture
    def runner(self):
        """Create a SimulationRunner with OrderLifecycle registered."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        return runner

    @pytest.fixture
    def scenario_runner(self, runner):
        """Create a ScenarioRunner wrapping the SimulationRunner."""
        return ScenarioRunner(runner)

    def test_happy_path_order(self, scenario_runner):
        """Execute happy path: created → validated → fulfilled → completed."""
        scenario = Scenario(
            name="happy_path_order",
            narrative="Order flows from creation to completion",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "customer_id": "cust_1",
                        "items": [{"sku": "WIDGET", "quantity": 2}],
                    },
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="fulfill",
                    args={"warehouse_id": "WH_EAST"},
                ),
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="complete"),
            ],
            expect=ExpectBlock(
                states={'OrderLifecycle("ord_1")': "completed"},
                events_emitted=["order.validated", "order.fulfilled", "order.completed"],
                events_not_emitted=["order.cancelled"],
            ),
        )

        result = scenario_runner.run(scenario)

        assert result.passed
        assert result.steps_executed == 4  # 1 create + 3 fires
        assert len(result.step_results) == 3  # Only fire steps return results

    def test_validation_failure_path(self, scenario_runner):
        """Order with invalid items goes to cancelled state."""
        scenario = Scenario(
            name="validation_failure",
            narrative="Invalid order is cancelled",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "customer_id": "cust_1",
                        "items": [],  # Empty items = invalid
                    },
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
            ],
            expect=ExpectBlock(
                states={'OrderLifecycle("ord_1")': "cancelled"},
                events_emitted=["order.cancelled"],
                events_not_emitted=["order.validated"],
            ),
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_cancellation_from_validated(self, scenario_runner):
        """Order can be cancelled after validation."""
        scenario = Scenario(
            name="cancel_validated",
            narrative="Validated order is cancelled",
            setup=[
                CreateStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    context={
                        "order_id": "ord_1",
                        "items": [{"sku": "WIDGET", "quantity": 1}],
                    },
                )
            ],
            steps=[
                FireStep(machine="OrderLifecycle", instance_id="ord_1", transition="validate"),
                FireStep(
                    machine="OrderLifecycle",
                    instance_id="ord_1",
                    transition="cancel",
                    args={"cancellation_reason": "customer_request"},
                ),
            ],
            expect=ExpectBlock(
                states={'OrderLifecycle("ord_1")': "cancelled"},
                events_emitted=["order.validated", "order.cancelled"],
            ),
        )

        result = scenario_runner.run(scenario)

        assert result.passed

    def test_parse_and_run_yaml_scenario(self, scenario_runner):
        """Can parse and run a YAML scenario file."""
        yaml_content = """
scenario: yaml_happy_path
narrative: "Happy path from YAML"

setup:
  - create: OrderLifecycle("ord_1")
    context:
      order_id: "ord_1"
      customer_id: "cust_1"
      items:
        - sku: "WIDGET"
          quantity: 2

steps:
  - fire: OrderLifecycle("ord_1").validate
  - fire: OrderLifecycle("ord_1").fulfill
    args:
      warehouse_id: "WH_EAST"
  - fire: OrderLifecycle("ord_1").complete

expect:
  states:
    OrderLifecycle("ord_1"): completed
  events_emitted:
    - order.validated
    - order.fulfilled
    - order.completed
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            scenario = parser.parse(f.name)
            result = scenario_runner.run(scenario)

            assert result.passed
            assert result.scenario_name == "yaml_happy_path"


# =============================================================================
# Tests for get_machine_class helper
# =============================================================================


class TestGetMachineClass:
    """Tests for SimulationRunner.get_machine_class helper."""

    def test_get_registered_machine_class(self):
        """Can get a registered machine class by name."""
        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        result = runner.get_machine_class("OrderLifecycle")

        assert result is OrderLifecycle

    def test_get_unregistered_machine_class(self):
        """Returns None for unregistered machine class."""
        runner = SimulationRunner()

        result = runner.get_machine_class("NonExistent")

        assert result is None


# =============================================================================
# Tests for EmitStep parsing and execution
# =============================================================================


class _SubscriberMachine(StateMachine):
    """Test fixture: subscribes to ``test.trigger`` and transitions to done."""

    waiting = State(initial=True)
    done = State(final=True)

    react = waiting.to(done)

    @classmethod
    def subscriptions(cls):
        return {"test.trigger": "react"}


class _SchemaMachine(StateMachine):
    """Test fixture: declares a schema requiring ``required_field``."""

    waiting = State(initial=True)
    done = State(final=True)

    react = waiting.to(done)

    EVENT_SCHEMAS = {
        "test.schemaful": {
            "required": ["required_field"],
            "properties": {"required_field": {"type": "string"}},
        }
    }

    @classmethod
    def subscriptions(cls):
        return {"test.schemaful": "react"}


class TestEmitStepParsing:
    """Tests for parsing ``- emit:`` step syntax."""

    def test_parse_emit_step_minimal(self):
        """Can parse minimal emit step with name only."""
        yaml_content = """
scenario: emit_minimal
narrative: "Minimal emit"
steps:
  - emit:
      name: foo.bar
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            scenario = ScenarioParser().parse(f.name)

            assert len(scenario.steps) == 1
            step = scenario.steps[0]
            assert isinstance(step, EmitStep)
            assert step.name == "foo.bar"
            assert step.payload == {}
            assert step.correlation_id == ""

    def test_parse_emit_step_with_payload(self):
        """Can parse emit step with payload and correlation_id."""
        yaml_content = """
scenario: emit_full
narrative: "Emit with payload"
steps:
  - emit:
      name: loan.return_requested
      payload:
        loan_id: "l1"
        condition: "good"
      correlation_id: "req-42"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            scenario = ScenarioParser().parse(f.name)

            step = scenario.steps[0]
            assert isinstance(step, EmitStep)
            assert step.name == "loan.return_requested"
            assert step.payload == {"loan_id": "l1", "condition": "good"}
            assert step.correlation_id == "req-42"

    def test_parse_emit_step_missing_name_raises(self):
        """Missing 'name' field is a parse error."""
        yaml_content = """
scenario: bad_emit
narrative: "Missing name"
steps:
  - emit:
      payload:
        loan_id: "l1"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="name"):
                parser.parse(f.name)

    def test_parse_emit_step_non_mapping_raises(self):
        """emit value must be a mapping."""
        yaml_content = """
scenario: bad_emit_shape
narrative: "Bad shape"
steps:
  - emit: "just-a-string"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="emit must be a mapping"):
                parser.parse(f.name)

    def test_parse_emit_step_non_mapping_payload_raises(self):
        """emit payload must be a mapping when present."""
        yaml_content = """
scenario: bad_emit_payload
narrative: "Bad payload"
steps:
  - emit:
      name: foo.bar
      payload: "not-a-dict"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="payload"):
                parser.parse(f.name)


class TestEmitStepExecution:
    """Tests for EmitStep execution and bus-routed cascades."""

    def test_emit_step_triggers_subscriber(self):
        """An emit step routes through the bus and fires a subscriber."""
        runner = SimulationRunner()
        runner.register(_SubscriberMachine)
        runner.register_resolver(_SubscriberMachine, lambda e: e.payload.get("id"))
        scenario_runner = ScenarioRunner(runner)

        scenario = Scenario(
            name="emit_triggers_subscriber",
            narrative="Emit routes to subscriber",
            steps=[
                EmitStep(name="test.trigger", payload={"id": "sub_1"}),
            ],
        )

        result = scenario_runner.run(scenario)

        # Scenario passes; subscriber was implicit-created and transitioned
        assert result.passed, result.failure_reason
        instance = runner.get(_SubscriberMachine, "sub_1")
        assert instance is not None
        assert instance.current_state == "done"

        # StepResult captures the routed transition
        assert len(result.step_results) == 1
        step_result = result.step_results[0]
        assert step_result.trigger == "emit:test.trigger"
        assert any(
            r.transition == "react" and r.target == "done"
            for r in step_result.transitions_fired
        )
        # The synthetic event itself appears in events_emitted
        assert any(e.name == "test.trigger" for e in step_result.events_emitted)

    def test_emit_step_synthetic_event_has_scenario_source(self):
        """The synthetic event is tagged with source_machine='_scenario'."""
        runner = SimulationRunner()
        runner.register(_SubscriberMachine)
        runner.register_resolver(_SubscriberMachine, lambda e: e.payload.get("id"))

        result = runner.emit_event(
            name="test.trigger",
            payload={"id": "sub_1"},
            correlation_id="corr-1",
        )

        synthetic = next(e for e in result.events_emitted if e.name == "test.trigger")
        assert synthetic.source_machine == "_scenario"
        assert synthetic.source_instance == ""
        assert synthetic.correlation_id == "corr-1"

    def test_emit_step_schema_violation_records_error(self):
        """Schema-violating emit is recorded as schema_validation_failed."""
        runner = SimulationRunner()
        runner.register(_SchemaMachine)
        runner.register_resolver(_SchemaMachine, lambda e: e.payload.get("id"))
        scenario_runner = ScenarioRunner(runner)

        scenario = Scenario(
            name="emit_schema_violation",
            narrative="Schema violation fails the scenario",
            steps=[
                # Missing required_field
                EmitStep(name="test.schemaful", payload={"id": "sub_1"}),
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "schema_validation_failed" in result.failure_reason

        step_result = result.step_results[0]
        assert len(step_result.errors) == 1
        err = step_result.errors[0]
        assert err.error_type == "schema_validation_failed"
        assert "required_field" in err.message

        # The bus rejected the event before logging it
        assert all(e.name != "test.schemaful" for e in runner.event_log)

    def test_emit_step_with_no_subscriber_does_not_crash(self):
        """Emitting an event no machine subscribes to is a silent no-op."""
        runner = SimulationRunner()
        runner.register(_SubscriberMachine)
        runner.register_resolver(_SubscriberMachine, lambda e: e.payload.get("id"))
        scenario_runner = ScenarioRunner(runner)

        scenario = Scenario(
            name="emit_no_subscriber",
            narrative="Unhandled emit is a no-op",
            steps=[
                EmitStep(name="nobody.cares", payload={"x": 1}),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed, result.failure_reason
        step_result = result.step_results[0]
        assert step_result.errors == []
        assert step_result.routing_failures == []
        assert step_result.transitions_fired == []
        # The bus still accepted and logged the event
        assert any(e.name == "nobody.cares" for e in step_result.events_emitted)

    def test_emit_step_in_yaml_end_to_end(self):
        """Full parse-and-run with an emit step in YAML."""
        runner = SimulationRunner()
        runner.register(_SubscriberMachine)
        runner.register_resolver(_SubscriberMachine, lambda e: e.payload.get("id"))
        scenario_runner = ScenarioRunner(runner)

        yaml_content = """
scenario: yaml_emit
narrative: "Emit step in YAML"
steps:
  - emit:
      name: test.trigger
      payload:
        id: "sub_1"
  - assert:
      states:
        _SubscriberMachine("sub_1"): done
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            scenario = ScenarioParser().parse(f.name)
            result = scenario_runner.run(scenario)

            assert result.passed, result.failure_reason
            instance = runner.get(_SubscriberMachine, "sub_1")
            assert instance is not None
            assert instance.current_state == "done"

    def test_emit_step_routing_failure_fails_scenario(self):
        """A routing failure (no resolver) fails the scenario and is recorded.

        The contract of an emit step is "I expect this event to land
        somewhere." A missing resolver or a resolver that returns None
        means the wiring is broken; a silent green run would be a footgun.
        """
        runner = SimulationRunner()
        runner.register(_SubscriberMachine)
        # Intentionally NOT registering a resolver
        scenario_runner = ScenarioRunner(runner)

        scenario = Scenario(
            name="emit_no_resolver",
            narrative="Emit without resolver fails the scenario",
            steps=[
                EmitStep(name="test.trigger", payload={"id": "sub_1"}),
            ],
        )

        result = scenario_runner.run(scenario)

        assert not result.passed
        assert "could not route" in result.failure_reason
        assert "no_resolver_registered" in result.failure_reason

        step_result = result.step_results[0]
        assert any(
            rf.target_machine == "_SubscriberMachine"
            and rf.reason == "no_resolver_registered"
            for rf in step_result.routing_failures
        )

    def test_emit_step_captures_downstream_cascade_emissions(self):
        """The subscriber's transition can emit further events; all land in step result."""

        class _CascadingSubscriber(StateMachine):
            waiting = State(initial=True)
            done = State(final=True)
            react = waiting.to(done)

            @classmethod
            def subscriptions(cls):
                return {"cascade.start": "react"}

            def on_enter_done(self):
                # The subscriber emits a downstream event when it transitions.
                self.emit("cascade.downstream", {"id": self._context.get("id")})

        runner = SimulationRunner()
        runner.register(_CascadingSubscriber)
        runner.register_resolver(_CascadingSubscriber, lambda e: e.payload.get("id"))
        scenario_runner = ScenarioRunner(runner)

        scenario = Scenario(
            name="emit_cascade",
            narrative="Emit triggers transition that emits more",
            steps=[
                EmitStep(name="cascade.start", payload={"id": "c1"}),
            ],
        )

        result = scenario_runner.run(scenario)

        assert result.passed, result.failure_reason

        # Both the synthetic event and the downstream emission land in the
        # step's events_emitted list — proving the runner captures the
        # entire cascade, not just the trigger event.
        step_result = result.step_results[0]
        emitted_names = [e.name for e in step_result.events_emitted]
        assert "cascade.start" in emitted_names
        assert "cascade.downstream" in emitted_names
        # And the downstream emission's source is the real machine, not the sentinel
        downstream = next(e for e in step_result.events_emitted if e.name == "cascade.downstream")
        assert downstream.source_machine == "_CascadingSubscriber"
        assert downstream.source_instance == "c1"


class TestEmitStepCorrelationIdValidation:
    """Type-validation for the optional ``correlation_id`` field."""

    def test_parse_emit_step_non_string_correlation_id_raises(self):
        """correlation_id must be a string when present."""
        yaml_content = """
scenario: bad_corr
narrative: "Bad correlation_id"
steps:
  - emit:
      name: foo.bar
      correlation_id: 42
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            parser = ScenarioParser()
            with pytest.raises(ScenarioParseError, match="correlation_id"):
                parser.parse(f.name)
