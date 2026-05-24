"""Tests for the virtual clock and @timeout decorator."""

import pytest

from sdd.protocol import State, StateMachine
from sdd.runner import SimulationRunner
from sdd.timing import timeout


class TestVirtualClockBasics:
    def test_clock_starts_at_zero(self):
        runner = SimulationRunner()
        assert runner.virtual_clock == 0.0

    def test_advance_time_moves_clock(self):
        runner = SimulationRunner()
        runner.advance_time(seconds=10)
        assert runner.virtual_clock == 10.0

    def test_advance_time_units_combine(self):
        runner = SimulationRunner()
        runner.advance_time(days=1, hours=2, minutes=3, seconds=4)
        assert runner.virtual_clock == 86400 + 2 * 3600 + 3 * 60 + 4

    def test_reset_zeroes_clock(self):
        runner = SimulationRunner()
        runner.advance_time(hours=1)
        runner.reset()
        assert runner.virtual_clock == 0.0


class TestTimeoutDecorator:
    def _build_payment_machine(self):
        class PaymentFlow(StateMachine):
            pending = State(initial=True)
            authorized = State(final=True)
            expired = State(final=True)

            authorize = pending.to(authorized)
            expire = pending.to(expired)

            @timeout(expire, hours=24)
            def on_deadline(self):
                self._context["expiry_reason"] = "authorization_timeout"

            @classmethod
            def subscriptions(cls):
                return {}

        return PaymentFlow

    def test_timeout_fires_after_deadline(self):
        P = self._build_payment_machine()
        runner = SimulationRunner()
        runner.register(P)
        runner.create(P, "p1", context={"id": "p1"})

        result = runner.advance_time(hours=25)
        # The expire transition should have fired
        instance = runner.get(P, "p1")
        assert instance.current_state == "expired"
        # The post-fire hook should have set context
        assert instance.context["expiry_reason"] == "authorization_timeout"
        # The step result should record it
        assert any(t.transition == "expire" for t in result.transitions_fired)

    def test_timeout_does_not_fire_before_deadline(self):
        P = self._build_payment_machine()
        runner = SimulationRunner()
        runner.register(P)
        runner.create(P, "p1", context={"id": "p1"})

        runner.advance_time(hours=23)
        instance = runner.get(P, "p1")
        assert instance.current_state == "pending"

    def test_timeout_cancelled_when_state_left_before_deadline(self):
        P = self._build_payment_machine()
        runner = SimulationRunner()
        runner.register(P)
        runner.create(P, "p1", context={"id": "p1"})

        # Move to authorized before the 24h deadline
        runner.advance_time(hours=10)
        runner.fire("p1", P, "authorize")
        # Advancing past the original deadline should NOT fire expire
        runner.advance_time(hours=20)
        instance = runner.get(P, "p1")
        assert instance.current_state == "authorized"

    def test_multiple_instances_independent_deadlines(self):
        P = self._build_payment_machine()
        runner = SimulationRunner()
        runner.register(P)
        runner.create(P, "p1", context={"id": "p1"})
        # p1 enters at t=0
        runner.advance_time(hours=10)
        # p2 enters at t=10h
        runner.create(P, "p2", context={"id": "p2"})
        # Advance another 15h → p1 has been pending 25h (expired), p2 only 15h
        runner.advance_time(hours=15)
        assert runner.get(P, "p1").current_state == "expired"
        assert runner.get(P, "p2").current_state == "pending"

    def test_timeout_with_zero_delay_rejected(self):
        with pytest.raises(ValueError):

            class Bad(StateMachine):
                a = State(initial=True)
                b = State(final=True)
                go = a.to(b)

                @timeout(go)  # no delay
                def hook(self):
                    pass

                @classmethod
                def subscriptions(cls):
                    return {}
