"""Tests for ConvergenceReport and runner.converge()."""

import pytest

from sdd.convergence import ConvergenceReport
from sdd.protocol import State, StateMachine
from sdd.runner import SimulationRunner
from sdd.scenario import CreateStep, ExpectBlock, FireStep, Scenario


def _build_minimal_system():
    class M(StateMachine):
        a = State(initial=True)
        b = State(final=True)
        go = a.to(b)

        @classmethod
        def subscriptions(cls):
            return {}

    runner = SimulationRunner()
    runner.register(M)
    return runner, M


def _scenario_for(machine_cls, instance_id, transition):
    return Scenario(
        name="t",
        narrative="",
        setup=[CreateStep(machine=machine_cls.__name__, instance_id=instance_id, context={})],
        steps=[FireStep(machine=machine_cls.__name__, instance_id=instance_id, transition=transition)],
        expect=ExpectBlock(states={f'{machine_cls.__name__}("{instance_id}")': "b"}),
    )


class TestConvergenceReport:
    def test_empty_report_is_not_converged(self):
        report = ConvergenceReport()
        assert report.is_converged is False
        assert "NOT RUN" in report.summary()

    def test_minimal_converged_system(self):
        runner, M = _build_minimal_system()
        scenario = _scenario_for(M, "x", "go")
        report = runner.converge(scenarios=[scenario])
        assert report.structural_passed is True
        assert report.scenarios_passed is True
        assert report.invariants_passed is True  # no invariants given
        assert report.coverage_complete is True
        assert report.is_converged is True
        assert "Ready for I/O adaptation" in report.summary()

    def test_converge_with_failing_invariant(self):
        runner, M = _build_minimal_system()
        scenario = _scenario_for(M, "x", "go")

        def invariant_always_fails(log, states):
            assert False, "deliberate failure"

        report = runner.converge(
            scenarios=[scenario],
            invariants=[invariant_always_fails],
        )
        assert report.invariants_passed is False
        assert report.is_converged is False
        assert "invariant violations" in report.summary()

    def test_converge_with_structural_issue(self):
        # Build a machine with an unreachable state
        class Bad(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            c = State(final=True)  # unreachable
            go = a.to(b)

            @classmethod
            def subscriptions(cls):
                return {}

        runner = SimulationRunner()
        runner.register(Bad)
        report = runner.converge(scenarios=[_scenario_for(Bad, "x", "go")])
        assert report.structural_passed is False
        assert "structural failures" in report.summary()

    def test_coverage_complete_only_when_all_transitions_fire(self):
        class M(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            c = State(final=True)
            go_b = a.to(b)
            go_c = a.to(c)

            @classmethod
            def subscriptions(cls):
                return {}

        runner = SimulationRunner()
        runner.register(M)
        scenario = Scenario(
            name="only_b",
            narrative="",
            setup=[CreateStep(machine="M", instance_id="x", context={})],
            steps=[FireStep(machine="M", instance_id="x", transition="go_b")],
            expect=ExpectBlock(states={'M("x")': "b"}),
        )
        report = runner.converge(scenarios=[scenario])
        assert report.scenarios_passed is True
        assert report.coverage_complete is False
        assert "incomplete coverage" in report.summary()

    def test_summary_format_has_three_sections(self):
        runner, M = _build_minimal_system()
        scenario = _scenario_for(M, "x", "go")
        report = runner.converge(scenarios=[scenario])
        summary = report.summary()
        assert "Structural Analysis:" in summary
        assert "Scenario Coverage:" in summary
        assert "Property Invariants:" in summary
