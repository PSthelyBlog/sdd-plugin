"""Scenario parser and runner for YAML-based scenario definitions."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

if TYPE_CHECKING:
    from sdd.events import Event, EventLog
    from sdd.invariants import InvariantReport
    from sdd.runner import SimulationRunner, StepResult


class ScenarioParseError(Exception):
    """Error during scenario parsing."""

    def __init__(self, message: str, file: str | None = None, line: int | None = None):
        self.file = file
        self.line = line
        location = ""
        if file:
            location = f" in {file}"
            if line:
                location += f" at line {line}"
        super().__init__(f"{message}{location}")


# Regex patterns for instance reference parsing
INSTANCE_REF_PATTERN = re.compile(r'^(\w+)\("([^"]+)"\)$')
FIRE_PATTERN = re.compile(r'^(\w+)\("([^"]+)"\)\.(\w+)$')


def parse_instance_ref(ref: str) -> tuple[str, str]:
    """
    Parse 'MachineName("id")' into (machine_name, instance_id).

    Raises:
        ScenarioParseError: If the format is invalid.
    """
    match = INSTANCE_REF_PATTERN.match(ref)
    if not match:
        raise ScenarioParseError(f"Invalid instance reference: {ref}")
    return match.group(1), match.group(2)


def parse_fire_ref(ref: str) -> tuple[str, str, str]:
    """
    Parse 'MachineName("id").transition' into (machine_name, instance_id, transition).

    Raises:
        ScenarioParseError: If the format is invalid.
    """
    match = FIRE_PATTERN.match(ref)
    if not match:
        raise ScenarioParseError(f"Invalid fire reference: {ref}")
    return match.group(1), match.group(2), match.group(3)


# Step Types


@dataclass
class CreateStep:
    """Instantiate a machine with initial context."""

    machine: str  # Machine class name
    instance_id: str  # Instance identifier
    context: dict = field(default_factory=dict)  # Initial context


@dataclass
class FireStep:
    """Fire a transition on a machine instance."""

    machine: str  # Machine class name
    instance_id: str  # Instance identifier
    transition: str  # Transition name
    args: dict = field(default_factory=dict)  # Optional kwargs for the transition
    expect_failure: str | None = None  # Expected failure reason, or None for success


@dataclass
class AdvanceTimeStep:
    """Move the virtual clock forward."""

    days: int = 0
    hours: int = 0
    minutes: int = 0


@dataclass
class AssertStep:
    """Mid-scenario assertion."""

    states: dict[str, str] | None = None  # MachineName("id"): expected_state
    events_emitted: list[str] | None = None  # Event names that should have been emitted
    events_not_emitted: list[str] | None = None  # Event names that should NOT have been emitted
    context: dict | None = None  # MachineName("id"): {field: value}


@dataclass
class EmitStep:
    """Inject a synthetic event onto the bus, routed as if by an adapter.

    Unlike ``FireStep``, which calls a transition directly on a known
    instance, ``EmitStep`` puts an event on the bus and lets the runner's
    resolvers route it to subscribers. Use this to exercise subscription
    paths whose triggering events are normally emitted only by inbound
    adapters.
    """

    name: str  # Event name (required)
    payload: dict = field(default_factory=dict)  # Event payload
    correlation_id: str = ""  # Optional correlation identifier


# Union type for all step types
Step = CreateStep | FireStep | AdvanceTimeStep | AssertStep | EmitStep


@dataclass
class ExpectBlock:
    """Final expectations after scenario execution."""

    states: dict[str, str] | None = None  # Expected final states
    events_emitted: list[str] | None = None  # Events that must have been emitted
    events_not_emitted: list[str] | None = None  # Events that must NOT have been emitted


@dataclass
class Scenario:
    """A complete scenario definition."""

    name: str  # Unique identifier
    narrative: str  # Human-readable description
    setup: list[Step] = field(default_factory=list)  # Setup steps (run before main steps)
    steps: list[Step] = field(default_factory=list)  # Main scenario steps
    expect: ExpectBlock = field(default_factory=ExpectBlock)  # Final expectations


class ScenarioParser:
    """Parses YAML scenario files into Scenario objects."""

    def parse(self, yaml_path: Path | str) -> Scenario:
        """
        Parse a single YAML file into a Scenario.

        Raises:
            ScenarioParseError: If the file is invalid or cannot be parsed.
        """
        path = Path(yaml_path)
        if not path.exists():
            raise ScenarioParseError(f"Scenario file not found: {path}", file=str(path))

        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ScenarioParseError(f"Invalid YAML: {e}", file=str(path))

        if not isinstance(data, dict):
            raise ScenarioParseError("Scenario must be a YAML mapping", file=str(path))

        return self._parse_scenario(data, str(path))

    def parse_directory(self, directory: Path | str) -> list[Scenario]:
        """
        Parse all YAML files in a directory.

        Returns:
            List of parsed Scenario objects.
        """
        path = Path(directory)
        if not path.is_dir():
            raise ScenarioParseError(f"Directory not found: {path}")

        scenarios = []
        for yaml_file in sorted(path.glob("**/*.yaml")):
            scenarios.append(self.parse(yaml_file))
        for yaml_file in sorted(path.glob("**/*.yml")):
            scenarios.append(self.parse(yaml_file))

        return scenarios

    def _parse_scenario(self, data: dict[str, Any], file: str) -> Scenario:
        """Parse scenario data into a Scenario object."""
        # Required fields
        if "scenario" not in data:
            raise ScenarioParseError("Missing required field: 'scenario'", file=file)

        name = data["scenario"]
        narrative = data.get("narrative", "")

        # Parse setup steps
        setup = []
        if "setup" in data:
            for i, step_data in enumerate(data["setup"]):
                try:
                    setup.append(self._parse_step(step_data))
                except ScenarioParseError as e:
                    raise ScenarioParseError(
                        f"Error in setup step {i}: {e}", file=file
                    )

        # Parse main steps
        steps = []
        if "steps" in data:
            for i, step_data in enumerate(data["steps"]):
                try:
                    steps.append(self._parse_step(step_data))
                except ScenarioParseError as e:
                    raise ScenarioParseError(
                        f"Error in step {i}: {e}", file=file
                    )

        # Parse expect block
        expect = ExpectBlock()
        if "expect" in data:
            expect = self._parse_expect_block(data["expect"], file)

        return Scenario(
            name=name,
            narrative=narrative,
            setup=setup,
            steps=steps,
            expect=expect,
        )

    def _parse_step(self, step_data: dict[str, Any]) -> Step:
        """Parse a single step from YAML data."""
        if not isinstance(step_data, dict):
            raise ScenarioParseError(f"Step must be a mapping, got {type(step_data).__name__}")

        if "create" in step_data:
            return self._parse_create_step(step_data)
        elif "fire" in step_data:
            return self._parse_fire_step(step_data)
        elif "advance_time" in step_data:
            return self._parse_advance_time_step(step_data)
        elif "assert" in step_data:
            return self._parse_assert_step(step_data)
        elif "emit" in step_data:
            return self._parse_emit_step(step_data)
        else:
            raise ScenarioParseError(
                f"Unknown step type. Expected 'create', 'fire', 'advance_time', 'assert', or 'emit'"
            )

    def _parse_create_step(self, step_data: dict[str, Any]) -> CreateStep:
        """Parse a create step."""
        ref = step_data["create"]
        machine, instance_id = parse_instance_ref(ref)
        context = step_data.get("context", {})
        return CreateStep(machine=machine, instance_id=instance_id, context=context)

    def _parse_fire_step(self, step_data: dict[str, Any]) -> FireStep:
        """Parse a fire step."""
        ref = step_data["fire"]
        machine, instance_id, transition = parse_fire_ref(ref)
        args = step_data.get("args", {})
        expect_failure = step_data.get("expect_failure")
        return FireStep(
            machine=machine,
            instance_id=instance_id,
            transition=transition,
            args=args if args else {},
            expect_failure=expect_failure,
        )

    def _parse_advance_time_step(self, step_data: dict[str, Any]) -> AdvanceTimeStep:
        """Parse an advance_time step."""
        time_data = step_data["advance_time"]
        if not isinstance(time_data, dict):
            raise ScenarioParseError("advance_time must be a mapping with days/hours/minutes")
        return AdvanceTimeStep(
            days=time_data.get("days", 0),
            hours=time_data.get("hours", 0),
            minutes=time_data.get("minutes", 0),
        )

    def _parse_emit_step(self, step_data: dict[str, Any]) -> EmitStep:
        """Parse an emit step."""
        emit_data = step_data["emit"]
        if not isinstance(emit_data, dict):
            raise ScenarioParseError(
                "emit must be a mapping with 'name' and optional 'payload'"
            )
        name = emit_data.get("name")
        if not isinstance(name, str) or not name:
            raise ScenarioParseError("emit step requires a 'name' field")
        payload = emit_data.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ScenarioParseError("emit 'payload' must be a mapping")
        correlation_id = emit_data.get("correlation_id", "")
        if correlation_id is None:
            correlation_id = ""
        if not isinstance(correlation_id, str):
            raise ScenarioParseError("emit 'correlation_id' must be a string")
        return EmitStep(
            name=name,
            payload=payload,
            correlation_id=correlation_id,
        )

    def _parse_assert_step(self, step_data: dict[str, Any]) -> AssertStep:
        """Parse an assert step."""
        assert_data = step_data["assert"]
        if not isinstance(assert_data, dict):
            raise ScenarioParseError("assert must be a mapping")

        # Parse states - convert from YAML format to our internal format
        states = assert_data.get("states")

        return AssertStep(
            states=states,
            events_emitted=assert_data.get("events_emitted"),
            events_not_emitted=assert_data.get("events_not_emitted"),
            context=assert_data.get("context"),
        )

    def _parse_expect_block(self, expect_data: dict[str, Any], file: str) -> ExpectBlock:
        """Parse the expect block."""
        if not isinstance(expect_data, dict):
            raise ScenarioParseError("expect must be a mapping", file=file)

        return ExpectBlock(
            states=expect_data.get("states"),
            events_emitted=expect_data.get("events_emitted"),
            events_not_emitted=expect_data.get("events_not_emitted"),
        )


@dataclass
class ScenarioResult:
    """Result of executing a scenario."""

    scenario_name: str
    passed: bool
    steps_executed: int
    step_results: list["StepResult"] = field(default_factory=list)  # Result of each fire step
    failure_step: int | None = None  # Index of failed step, if any
    failure_reason: str | None = None  # Why it failed
    final_states: dict[str, str] = field(default_factory=dict)  # Machine states after execution
    events_emitted: list["Event"] = field(default_factory=list)  # All events from the run
    invariant_results: "InvariantReport | None" = None  # Invariant check results, if any


class ScenarioRunner:
    """Executes scenarios against a SimulationRunner."""

    def __init__(self, runner: "SimulationRunner") -> None:
        self._runner = runner

    def run(
        self,
        scenario: Scenario,
        invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] | None = None,
    ) -> ScenarioResult:
        """
        Execute a scenario and return the result.

        Args:
            scenario: The scenario to execute.
            invariants: Optional list of invariant functions to check after execution.

        Steps:
        1. Reset the runner
        2. Execute setup steps
        3. Execute main steps in order
        4. If any step fails unexpectedly, halt and record failure
        5. After all steps, verify expect block
        6. Check invariants (if provided)
        7. Return ScenarioResult
        """
        # Reset runner for clean state
        self._runner.reset()

        step_results: list["StepResult"] = []
        steps_executed = 0
        failure_step = None
        failure_reason = None

        # Execute setup steps
        all_steps = scenario.setup + scenario.steps
        for i, step in enumerate(all_steps):
            try:
                result = self._execute_step(step)
                steps_executed += 1

                if result is not None:
                    step_results.append(result)

                    # Check for unexpected failures in FireStep
                    if isinstance(step, FireStep):
                        has_errors = len(result.errors) > 0
                        if step.expect_failure:
                            # We expected a failure
                            if not has_errors:
                                failure_step = i
                                failure_reason = (
                                    f"Expected failure '{step.expect_failure}' "
                                    f"but transition succeeded"
                                )
                                break
                            # Check if we got the expected failure type
                            actual_failure = result.errors[0].error_type if result.errors else None
                            if actual_failure != step.expect_failure:
                                failure_step = i
                                failure_reason = (
                                    f"Expected failure '{step.expect_failure}' "
                                    f"but got '{actual_failure}'"
                                )
                                break
                        else:
                            # We expected success
                            if has_errors:
                                error = result.errors[0]
                                failure_step = i
                                failure_reason = (
                                    f"Transition '{step.transition}' failed: "
                                    f"{error.error_type} - {error.message}"
                                )
                                break
                    elif isinstance(step, EmitStep):
                        # Emit steps have no expect_failure concept. The
                        # contract of an emit step is "I expect this event to
                        # land somewhere," so both errors in the routed
                        # cascade and routing failures (no resolver, resolver
                        # returned None, resolver raised) fail the scenario.
                        # Without this, a typo'd resolver or missing
                        # registration would silently produce a green run.
                        if result.errors:
                            error = result.errors[0]
                            failure_step = i
                            failure_reason = (
                                f"Emit '{step.name}' produced error: "
                                f"{error.error_type} - {error.message}"
                            )
                            break
                        if result.routing_failures:
                            rf = result.routing_failures[0]
                            failure_step = i
                            failure_reason = (
                                f"Emit '{step.name}' could not route to "
                                f"{rf.target_machine}.{rf.target_transition}: "
                                f"{rf.reason} - {rf.message}"
                            )
                            break

            except Exception as e:
                failure_step = i
                failure_reason = str(e)
                break

        # Collect final states
        final_states = {}
        for (machine_class, instance_id), state in self._runner.machine_states().items():
            key = f'{machine_class.__name__}("{instance_id}")'
            final_states[key] = state

        # Collect all events
        events_emitted = list(self._runner.event_log)

        # If no failure so far, verify expect block
        if failure_step is None:
            expect_failure = self._verify_expect_block(
                scenario.expect, final_states, events_emitted
            )
            if expect_failure:
                failure_step = steps_executed  # After all steps
                failure_reason = expect_failure

        # Check invariants if provided and scenario passed so far
        invariant_results: "InvariantReport | None" = None
        if invariants:
            from sdd.invariants import check_invariants

            # Build states dict in the format invariants expect: (machine_name, instance_id) -> state
            states_for_invariants: dict[tuple[str, str], str] = {}
            for (machine_class, instance_id), state in self._runner.machine_states().items():
                states_for_invariants[(machine_class.__name__, instance_id)] = state

            invariant_results = check_invariants(
                invariants,
                self._runner.event_log,
                states_for_invariants,
            )

            # If invariants failed and scenario otherwise passed, mark as failed
            if failure_step is None and not invariant_results.all_passed:
                failure_step = steps_executed  # After all steps
                # Format failure reason from first invariant violation
                first_failure = invariant_results.failures[0]
                failure_reason = (
                    f"Invariant '{first_failure.invariant_name}' violated: "
                    f"{first_failure.violation_details}"
                )

        return ScenarioResult(
            scenario_name=scenario.name,
            passed=(failure_step is None),
            steps_executed=steps_executed,
            step_results=step_results,
            failure_step=failure_step,
            failure_reason=failure_reason,
            final_states=final_states,
            events_emitted=events_emitted,
            invariant_results=invariant_results,
        )

    def run_all(
        self,
        scenarios: list[Scenario],
        invariants: list[Callable[["EventLog", dict[tuple[str, str], str]], None]] | None = None,
    ) -> list[ScenarioResult]:
        """
        Execute multiple scenarios, resetting between each.

        Args:
            scenarios: List of scenarios to execute.
            invariants: Optional list of invariant functions to check after each scenario.

        Returns:
            List of ScenarioResult for each scenario.
        """
        return [self.run(scenario, invariants=invariants) for scenario in scenarios]

    def _execute_step(self, step: Step) -> "StepResult | None":
        """
        Execute a single step.

        Returns StepResult for FireStep, None for other steps.
        """
        if isinstance(step, CreateStep):
            self._execute_create_step(step)
            return None
        elif isinstance(step, FireStep):
            return self._execute_fire_step(step)
        elif isinstance(step, AdvanceTimeStep):
            self._execute_advance_time_step(step)
            return None
        elif isinstance(step, AssertStep):
            self._execute_assert_step(step)
            return None
        elif isinstance(step, EmitStep):
            return self._execute_emit_step(step)
        else:
            raise ScenarioParseError(f"Unknown step type: {type(step)}")

    def _execute_create_step(self, step: CreateStep) -> None:
        """Execute a CreateStep."""
        machine_class = self._runner.get_machine_class(step.machine)
        if machine_class is None:
            raise ScenarioParseError(
                f"Unknown machine class: {step.machine}. "
                f"Make sure to register it with the runner."
            )
        self._runner.create(machine_class, step.instance_id, step.context)

    def _execute_fire_step(self, step: FireStep) -> "StepResult":
        """Execute a FireStep and return the result."""
        machine_class = self._runner.get_machine_class(step.machine)
        if machine_class is None:
            raise ScenarioParseError(
                f"Unknown machine class: {step.machine}. "
                f"Make sure to register it with the runner."
            )
        return self._runner.fire(step.instance_id, machine_class, step.transition, **step.args)

    def _execute_emit_step(self, step: EmitStep) -> "StepResult":
        """Execute an EmitStep by injecting a synthetic event onto the bus."""
        return self._runner.emit_event(
            name=step.name,
            payload=step.payload,
            correlation_id=step.correlation_id,
        )

    def _execute_advance_time_step(self, step: AdvanceTimeStep) -> None:
        """Execute an AdvanceTimeStep by advancing the runner's virtual clock."""
        self._runner.advance_time(
            days=step.days,
            hours=step.hours,
            minutes=step.minutes,
        )

    def _execute_assert_step(self, step: AssertStep) -> None:
        """Execute an AssertStep, raising on failure."""
        # Verify states
        if step.states:
            for ref, expected_state in step.states.items():
                machine, instance_id = parse_instance_ref(ref)
                machine_class = self._runner.get_machine_class(machine)
                if machine_class is None:
                    raise AssertionError(f"Unknown machine class in assertion: {machine}")
                instance = self._runner.get(machine_class, instance_id)
                if instance is None:
                    raise AssertionError(
                        f"Instance {ref} not found for state assertion"
                    )
                if instance.current_state != expected_state:
                    raise AssertionError(
                        f"State assertion failed for {ref}: "
                        f"expected '{expected_state}', got '{instance.current_state}'"
                    )

        # Verify events_emitted
        if step.events_emitted:
            emitted_names = {e.name for e in self._runner.event_log}
            for event_name in step.events_emitted:
                if event_name not in emitted_names:
                    raise AssertionError(
                        f"Event assertion failed: expected '{event_name}' to be emitted"
                    )

        # Verify events_not_emitted
        if step.events_not_emitted:
            emitted_names = {e.name for e in self._runner.event_log}
            for event_name in step.events_not_emitted:
                if event_name in emitted_names:
                    raise AssertionError(
                        f"Event assertion failed: expected '{event_name}' to NOT be emitted"
                    )

        # Verify context (deferred feature - log warning if used)
        if step.context:
            warnings.warn(
                "Context assertions are not yet implemented. Skipping context assertion.",
                stacklevel=2,
            )

    def _verify_expect_block(
        self,
        expect: ExpectBlock,
        final_states: dict[str, str],
        events: list["Event"],
    ) -> str | None:
        """
        Verify the expect block. Returns failure reason or None if passed.
        """
        # Verify final states
        if expect.states:
            for ref, expected_state in expect.states.items():
                if ref not in final_states:
                    return f"Expected state for {ref} but instance not found"
                if final_states[ref] != expected_state:
                    return (
                        f"Expected state '{expected_state}' for {ref}, "
                        f"got '{final_states[ref]}'"
                    )

        # Verify events_emitted
        if expect.events_emitted:
            emitted_names = {e.name for e in events}
            for event_name in expect.events_emitted:
                if event_name not in emitted_names:
                    return f"Expected event '{event_name}' was not emitted"

        # Verify events_not_emitted
        if expect.events_not_emitted:
            emitted_names = {e.name for e in events}
            for event_name in expect.events_not_emitted:
                if event_name in emitted_names:
                    return f"Event '{event_name}' was emitted but should not have been"

        return None
