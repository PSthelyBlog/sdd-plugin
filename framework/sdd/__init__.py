"""Simulation-Driven Development Framework."""

from sdd.protocol import State, StateMachine, TransitionResult, TransitionRecord
from sdd.events import Event, EventBus, EventLog, SchemaValidationError, validate_payload
from sdd.runner import (
    SimulationRunner,
    StepResult,
    StepError,
    StructuralReport,
    RoutingFailure,
)
from sdd.scenario import (
    CreateStep,
    FireStep,
    AdvanceTimeStep,
    AssertStep,
    ExpectBlock,
    Scenario,
    ScenarioParser,
    ScenarioRunner,
    ScenarioResult,
    ScenarioParseError,
)
from sdd.invariants import (
    InvariantResult,
    InvariantReport,
    check_invariant,
    check_invariants,
    load_invariants,
    load_invariants_from_module,
)
from sdd.convergence import ConvergenceReport
from sdd.adapters import Adapter, InstanceLoader, ProductionRunner
from sdd.timing import timeout

__all__ = [
    # protocol
    "State",
    "StateMachine",
    "TransitionResult",
    "TransitionRecord",
    # events
    "Event",
    "EventBus",
    "EventLog",
    "SchemaValidationError",
    "validate_payload",
    # runner
    "SimulationRunner",
    "StepResult",
    "StepError",
    "StructuralReport",
    "RoutingFailure",
    # scenario
    "CreateStep",
    "FireStep",
    "AdvanceTimeStep",
    "AssertStep",
    "ExpectBlock",
    "Scenario",
    "ScenarioParser",
    "ScenarioRunner",
    "ScenarioResult",
    "ScenarioParseError",
    # invariants
    "InvariantResult",
    "InvariantReport",
    "check_invariant",
    "check_invariants",
    "load_invariants",
    "load_invariants_from_module",
    # convergence
    "ConvergenceReport",
    # adapters
    "Adapter",
    "InstanceLoader",
    "ProductionRunner",
    # timing
    "timeout",
]
