"""Tests for the Adapter protocol and ProductionRunner."""

import pytest

from sdd.adapters import Adapter, ProductionRunner
from sdd.events import Event
from sdd.protocol import State, StateMachine


class _Machine(StateMachine):
    a = State(initial=True)
    b = State(final=True)
    go = a.to(b)

    def on_enter_b(self):
        self.emit("m.done", {"id": self._context.get("id")})

    @classmethod
    def subscriptions(cls):
        return {}


class TestAdapterProtocol:
    def test_protocol_runtime_check(self):
        class MyAdapter:
            def attach(self, runner):
                pass

        assert isinstance(MyAdapter(), Adapter)

    def test_object_without_attach_is_not_adapter(self):
        class NoAttach:
            pass

        assert not isinstance(NoAttach(), Adapter)


class TestProductionRunnerLifecycle:
    def test_attach_calls_adapter_attach(self):
        calls = []

        class Recorder:
            def attach(self, runner):
                calls.append(("attach", runner))

            def startup(self):
                calls.append(("startup",))

            def shutdown(self):
                calls.append(("shutdown",))

        runner = ProductionRunner()
        adapter = Recorder()
        runner.attach(adapter)
        assert calls == [("attach", runner)]
        runner.startup()
        assert ("startup",) in calls
        assert runner.started is True
        runner.shutdown()
        assert ("shutdown",) in calls
        assert runner.started is False

    def test_shutdown_order_is_reverse_of_attach(self):
        events = []

        def make_recorder(tag):
            class A:
                def attach(self, runner):
                    pass

                def startup(self):
                    events.append(f"start:{tag}")

                def shutdown(self):
                    events.append(f"stop:{tag}")

            return A()

        runner = ProductionRunner()
        runner.attach(make_recorder("first"))
        runner.attach(make_recorder("second"))
        runner.startup()
        runner.shutdown()
        assert events == ["start:first", "start:second", "stop:second", "stop:first"]

    def test_context_manager(self):
        events = []

        class A:
            def attach(self, r):
                pass

            def startup(self):
                events.append("up")

            def shutdown(self):
                events.append("down")

        runner = ProductionRunner()
        runner.attach(A())
        with runner:
            assert runner.started is True
        assert events == ["up", "down"]
        assert runner.started is False

    def test_outbound_adapter_receives_events(self):
        received = []

        class EventLogger:
            def attach(self, runner):
                runner.event_bus.subscribe("m.done", lambda e: received.append(e))

        runner = ProductionRunner()
        runner.register(_Machine)
        runner.attach(EventLogger())

        runner.create(_Machine, "x", context={"id": "x"})
        runner.fire("x", _Machine, "go")

        assert len(received) == 1
        assert received[0].name == "m.done"


class TestInstanceLoader:
    def test_loader_called_when_instance_missing(self):
        load_calls = []

        def loader(machine_class, instance_id):
            load_calls.append((machine_class.__name__, instance_id))
            # Rehydrate from "storage": snapshot a fresh instance
            return machine_class(context={"id": instance_id})

        runner = ProductionRunner()
        runner.register(_Machine)
        runner.set_instance_loader(loader)

        # get() for non-existent instance triggers loader
        instance = runner.get(_Machine, "rehydrated_x")
        assert instance is not None
        assert load_calls == [("_Machine", "rehydrated_x")]
        # And subsequent gets find it in memory (no second load)
        instance2 = runner.get(_Machine, "rehydrated_x")
        assert instance2 is instance
        assert len(load_calls) == 1

    def test_loader_returning_none_leaves_instance_missing(self):
        runner = ProductionRunner()
        runner.register(_Machine)
        runner.set_instance_loader(lambda cls, iid: None)
        assert runner.get(_Machine, "nope") is None

    def test_no_loader_returns_none_for_missing(self):
        runner = ProductionRunner()
        runner.register(_Machine)
        assert runner.get(_Machine, "nope") is None

    def test_loaded_instance_is_connected_to_bus(self):
        def loader(cls, iid):
            return cls(context={"id": iid})

        runner = ProductionRunner()
        runner.register(_Machine)
        runner.set_instance_loader(loader)

        instance = runner.get(_Machine, "x")
        # Loaded instance should have the runner's bus attached
        assert instance._event_bus is runner.event_bus
        assert instance._instance_id == "x"

        # And firing on it actually delivers events through the bus
        runner.fire("x", _Machine, "go")
        assert len(runner.event_log) == 1


class TestProductionRunnerInheritsSimulation:
    def test_structural_check_works(self):
        runner = ProductionRunner()
        runner.register(_Machine)
        report = runner.check()
        assert "_Machine" in report.machines_analyzed

    def test_converge_works(self):
        """Confirm ProductionRunner inherits SimulationRunner.converge cleanly."""
        from sdd.scenario import CreateStep, ExpectBlock, FireStep, Scenario

        runner = ProductionRunner()
        runner.register(_Machine)
        scenario = Scenario(
            name="t",
            narrative="",
            setup=[CreateStep(machine="_Machine", instance_id="x", context={"id": "x"})],
            steps=[FireStep(machine="_Machine", instance_id="x", transition="go")],
            expect=ExpectBlock(states={'_Machine("x")': "b"}),
        )
        report = runner.converge(scenarios=[scenario])
        # _Machine emits m.done with no subscriber → dead letter → not is_converged.
        # But the scenario itself passed and coverage is complete — confirm those.
        assert report.scenarios_passed is True
        assert report.coverage_complete is True
        assert report.structural is not None
        assert "m.done" in report.structural.dead_letters
