"""Tests for coverage tracking in SimulationRunner."""

from sdd.protocol import State, StateMachine
from sdd.runner import SimulationRunner
from sdd.scenario import (
    CreateStep,
    ExpectBlock,
    FireStep,
    Scenario,
    ScenarioRunner,
)


def _two_path_machine_cls():
    """Build a machine with two paths from initial → final."""
    class Splitter(StateMachine):
        start = State(initial=True)
        a = State()
        b = State()
        done = State(final=True)

        go = start.to(a) | start.to(b)
        finish_a = a.to(done)
        finish_b = b.to(done)

        def guard_go_to_a(self, **kwargs):
            return kwargs.get("branch") == "a"
        # go_to_b is the fallback

        @classmethod
        def subscriptions(cls):
            return {}

    return Splitter


def _run_scenario(runner, machine_cls, instance_id, *steps, expected_states=None):
    """Build and run a one-off scenario, return the result."""
    setup = [CreateStep(machine=machine_cls.__name__, instance_id=instance_id, context={})]
    expect = ExpectBlock(states=expected_states or {})
    scenario = Scenario(name="t", narrative="", setup=setup, steps=list(steps), expect=expect)
    sr = ScenarioRunner(runner)
    return sr.run(scenario)


class TestTransitionCoverage:
    def test_one_branch_covered(self):
        Splitter = _two_path_machine_cls()
        runner = SimulationRunner()
        runner.register(Splitter)

        result = _run_scenario(
            runner,
            Splitter,
            "x1",
            FireStep(machine="Splitter", instance_id="x1", transition="go", args={"branch": "a"}),
            FireStep(machine="Splitter", instance_id="x1", transition="finish_a"),
        )

        cov = runner.transition_coverage([result])["Splitter"]
        assert cov["total"] == 4  # (go,start,a), (go,start,b), (finish_a,a,done), (finish_b,b,done)
        assert ("go", "start", "a") in cov["covered"]
        assert ("finish_a", "a", "done") in cov["covered"]
        # The other branch + finish_b not exercised
        assert ("go", "start", "b") in cov["missing"]
        assert ("finish_b", "b", "done") in cov["missing"]
        assert cov["coverage"] == 0.5

    def test_full_coverage_both_branches(self):
        Splitter = _two_path_machine_cls()
        runner = SimulationRunner()
        runner.register(Splitter)

        r1 = _run_scenario(
            runner, Splitter, "x1",
            FireStep(machine="Splitter", instance_id="x1", transition="go", args={"branch": "a"}),
            FireStep(machine="Splitter", instance_id="x1", transition="finish_a"),
        )
        r2 = _run_scenario(
            runner, Splitter, "x2",
            FireStep(machine="Splitter", instance_id="x2", transition="go", args={"branch": "b"}),
            FireStep(machine="Splitter", instance_id="x2", transition="finish_b"),
        )

        cov = runner.transition_coverage([r1, r2])["Splitter"]
        assert cov["coverage"] == 1.0
        assert cov["missing"] == []


class TestStateCoverage:
    def test_initial_state_always_covered(self):
        Splitter = _two_path_machine_cls()
        runner = SimulationRunner()
        runner.register(Splitter)

        result = _run_scenario(runner, Splitter, "x", )

        cov = runner.state_coverage([result])["Splitter"]
        assert "start" in cov["entered"]
        assert "a" in cov["missing"]

    def test_state_covered_after_firing(self):
        Splitter = _two_path_machine_cls()
        runner = SimulationRunner()
        runner.register(Splitter)
        result = _run_scenario(
            runner, Splitter, "x",
            FireStep(machine="Splitter", instance_id="x", transition="go", args={"branch": "a"}),
        )
        cov = runner.state_coverage([result])["Splitter"]
        assert "a" in cov["entered"]


class TestEventPathCoverage:
    def test_subscription_triggered(self):
        class Producer(StateMachine):
            EVENT_SCHEMAS = {"prod.done": {"required": ["id"]}}
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

            def on_enter_b(self):
                self.emit("prod.done", {"id": self._context.get("id")})

            @classmethod
            def subscriptions(cls):
                return {}

        class Consumer(StateMachine):
            waiting = State(initial=True)
            done = State(final=True)
            consume = waiting.to(done)

            @classmethod
            def subscriptions(cls):
                return {"prod.done": "consume"}

        runner = SimulationRunner()
        runner.register(Producer)
        runner.register(Consumer)
        runner.register_resolver(Consumer, lambda e: e.payload.get("id"))

        result = _run_scenario(
            runner, Producer, "x",
            FireStep(machine="Producer", instance_id="x", transition="go", args={"id": "x"}),
        )

        cov = runner.event_path_coverage([result])
        assert cov["coverage"] == 1.0
        assert ("Consumer", "prod.done") in cov["triggered"]
