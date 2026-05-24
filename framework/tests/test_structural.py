"""Tests for structural validation (Phase 5)."""

import pytest

from sdd.protocol import State, StateMachine
from sdd.runner import SimulationRunner, StructuralReport


# =============================================================================
# Test Machines for Structural Analysis
# =============================================================================


class WellFormedMachine(StateMachine):
    """A machine with all states reachable and all paths leading to final."""

    initial = State(initial=True)
    processing = State()
    done = State(final=True)

    start = initial.to(processing)
    finish = processing.to(done)

    def on_enter_done(self) -> None:
        self.emit("wellformed.done", {"id": self._context.get("id")})

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


class MachineWithUnreachableState(StateMachine):
    """A machine with a state that cannot be reached."""

    initial = State(initial=True)
    reachable = State()
    unreachable = State()  # No transition targets this
    done = State(final=True)

    go = initial.to(reachable)
    finish = reachable.to(done)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


class MachineWithDeadlock(StateMachine):
    """A machine with a state that has no path to final."""

    initial = State(initial=True)
    stuck = State()  # Can get here but cannot leave
    done = State(final=True)

    # Can go to stuck or done from initial
    proceed = initial.to(stuck) | initial.to(done)

    def guard_proceed_to_stuck(self, **kwargs) -> bool:
        return kwargs.get("go_stuck", False)

    def guard_proceed_to_done(self, **kwargs) -> bool:
        return not kwargs.get("go_stuck", False)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


class EmitterMachine(StateMachine):
    """A machine that emits events."""

    initial = State(initial=True)
    emitting = State()
    done = State(final=True)

    emit_step = initial.to(emitting)
    finish = emitting.to(done)

    def on_enter_emitting(self) -> None:
        self.emit("emitter.started", {"id": self._context.get("id")})

    def on_enter_done(self) -> None:
        self.emit("emitter.completed", {"id": self._context.get("id")})

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


class SubscriberMachine(StateMachine):
    """A machine that subscribes to events."""

    initial = State(initial=True)
    processed = State()
    done = State(final=True)

    handle = initial.to(processed)
    complete = processed.to(done)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "emitter.started": "handle",
            "emitter.completed": "complete",
        }


class PhantomSubscriberMachine(StateMachine):
    """A machine that subscribes to a non-existent event."""

    initial = State(initial=True)
    done = State(final=True)

    handle = initial.to(done)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {
            "nonexistent.event": "handle",
        }


class MultiPathMachine(StateMachine):
    """A machine with multiple paths to final state."""

    initial = State(initial=True)
    path_a = State()
    path_b = State()
    merged = State()
    done = State(final=True)

    choose = initial.to(path_a) | initial.to(path_b)
    from_a = path_a.to(merged)
    from_b = path_b.to(merged)
    finish = merged.to(done)

    def guard_choose_to_path_a(self, **kwargs) -> bool:
        return kwargs.get("choice") == "a"
    # guard_choose_to_path_b omitted intentionally — path_b is the fallback

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


class CyclicMachine(StateMachine):
    """A machine with a cycle that still reaches final."""

    initial = State(initial=True)
    working = State()
    retry = State()
    done = State(final=True)

    start = initial.to(working)
    fail = working.to(retry)
    rework = retry.to(working)  # Cycle back
    succeed = working.to(done)

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        return {}


# =============================================================================
# Test StructuralReport
# =============================================================================


class TestStructuralReport:
    """Tests for the StructuralReport dataclass."""

    def test_is_valid_with_no_issues(self):
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={},
            terminal_states={},
            dead_letters=[],
            phantom_subscriptions=[],
        )
        assert report.is_valid is True

    def test_is_valid_with_unreachable_states(self):
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={"TestMachine": ["orphan"]},
            terminal_states={},
            dead_letters=[],
            phantom_subscriptions=[],
        )
        assert report.is_valid is False

    def test_is_valid_with_terminal_states(self):
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={},
            terminal_states={"TestMachine": ["stuck"]},
            dead_letters=[],
            phantom_subscriptions=[],
        )
        assert report.is_valid is False

    def test_is_valid_with_dead_letters(self):
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={},
            terminal_states={},
            dead_letters=["orphan.event"],
            phantom_subscriptions=[],
        )
        assert report.is_valid is False

    def test_is_valid_with_phantom_subscriptions(self):
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={},
            terminal_states={},
            dead_letters=[],
            phantom_subscriptions=["ghost.event"],
        )
        assert report.is_valid is False

    def test_is_valid_with_empty_unreachable_dict(self):
        """Empty dict in unreachable_states should still be valid."""
        report = StructuralReport(
            machines_analyzed=["TestMachine"],
            total_states=3,
            total_transitions=2,
            unreachable_states={"TestMachine": []},  # Empty list
            terminal_states={},
            dead_letters=[],
            phantom_subscriptions=[],
        )
        assert report.is_valid is True


# =============================================================================
# Test Reachability Analysis
# =============================================================================


class TestReachability:
    """Tests for reachability analysis."""

    def test_all_states_reachable(self):
        runner = SimulationRunner()
        runner.register(WellFormedMachine)

        reachable = runner.reachability(WellFormedMachine)
        assert reachable == {"initial", "processing", "done"}

    def test_unreachable_state_not_in_reachability(self):
        runner = SimulationRunner()
        runner.register(MachineWithUnreachableState)

        reachable = runner.reachability(MachineWithUnreachableState)
        assert "unreachable" not in reachable
        assert reachable == {"initial", "reachable", "done"}

    def test_multi_path_all_reachable(self):
        runner = SimulationRunner()
        runner.register(MultiPathMachine)

        reachable = runner.reachability(MultiPathMachine)
        assert reachable == {"initial", "path_a", "path_b", "merged", "done"}

    def test_cyclic_machine_all_reachable(self):
        runner = SimulationRunner()
        runner.register(CyclicMachine)

        reachable = runner.reachability(CyclicMachine)
        assert reachable == {"initial", "working", "retry", "done"}


# =============================================================================
# Test Dead States Detection
# =============================================================================


class TestDeadStates:
    """Tests for dead states detection."""

    def test_no_dead_states(self):
        runner = SimulationRunner()
        runner.register(WellFormedMachine)

        dead = runner.dead_states(WellFormedMachine)
        assert dead == set()

    def test_detects_unreachable_state(self):
        runner = SimulationRunner()
        runner.register(MachineWithUnreachableState)

        dead = runner.dead_states(MachineWithUnreachableState)
        assert dead == {"unreachable"}

    def test_multi_path_no_dead_states(self):
        runner = SimulationRunner()
        runner.register(MultiPathMachine)

        dead = runner.dead_states(MultiPathMachine)
        assert dead == set()


# =============================================================================
# Test Termination Issues Detection
# =============================================================================


class TestTerminationIssues:
    """Tests for termination issues detection."""

    def test_no_termination_issues(self):
        runner = SimulationRunner()
        runner.register(WellFormedMachine)

        issues = runner.termination_issues(WellFormedMachine)
        assert issues == set()

    def test_detects_deadlock_state(self):
        runner = SimulationRunner()
        runner.register(MachineWithDeadlock)

        issues = runner.termination_issues(MachineWithDeadlock)
        assert issues == {"stuck"}

    def test_cyclic_machine_no_termination_issues(self):
        """Cycle with exit to final should not be flagged."""
        runner = SimulationRunner()
        runner.register(CyclicMachine)

        issues = runner.termination_issues(CyclicMachine)
        assert issues == set()

    def test_multi_path_no_termination_issues(self):
        runner = SimulationRunner()
        runner.register(MultiPathMachine)

        issues = runner.termination_issues(MultiPathMachine)
        assert issues == set()


# =============================================================================
# Test Event Emission Detection
# =============================================================================


class TestEmitDetection:
    """Tests for detecting emitted events."""

    def test_finds_emitted_events(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)

        emitted = runner._find_emitted_events(EmitterMachine)
        assert emitted == {"emitter.started", "emitter.completed"}

    def test_machine_with_no_emits(self):
        runner = SimulationRunner()
        runner.register(WellFormedMachine)

        # WellFormedMachine has one emit in on_enter_done
        emitted = runner._find_emitted_events(WellFormedMachine)
        assert emitted == {"wellformed.done"}

    def test_subscriber_emits_nothing(self):
        runner = SimulationRunner()
        runner.register(SubscriberMachine)

        emitted = runner._find_emitted_events(SubscriberMachine)
        assert emitted == set()


# =============================================================================
# Test Dead Letters Detection
# =============================================================================


class TestDeadLetters:
    """Tests for dead letters detection."""

    def test_no_dead_letters_when_all_subscribed(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)
        runner.register(SubscriberMachine)

        dead = runner.dead_letters()
        assert dead == []

    def test_detects_unsubscribed_events(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)
        # No subscriber registered

        dead = runner.dead_letters()
        assert "emitter.started" in dead
        assert "emitter.completed" in dead

    def test_partial_subscription(self):
        """Test when some events are subscribed and some are not."""

        class PartialSubscriber(StateMachine):
            initial = State(initial=True)
            done = State(final=True)
            handle = initial.to(done)

            @classmethod
            def subscriptions(cls) -> dict[str, str]:
                return {"emitter.started": "handle"}  # Only subscribes to one

        runner = SimulationRunner()
        runner.register(EmitterMachine)
        runner.register(PartialSubscriber)

        dead = runner.dead_letters()
        assert "emitter.completed" in dead
        assert "emitter.started" not in dead


# =============================================================================
# Test Phantom Subscriptions Detection
# =============================================================================


class TestPhantomSubscriptions:
    """Tests for phantom subscriptions detection."""

    def test_no_phantom_subscriptions_when_all_emitted(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)
        runner.register(SubscriberMachine)

        phantom = runner.phantom_subscriptions()
        assert phantom == []

    def test_detects_subscription_to_nonexistent_event(self):
        runner = SimulationRunner()
        runner.register(PhantomSubscriberMachine)

        phantom = runner.phantom_subscriptions()
        assert phantom == ["nonexistent.event"]

    def test_subscription_without_emitter(self):
        runner = SimulationRunner()
        runner.register(SubscriberMachine)
        # EmitterMachine not registered

        phantom = runner.phantom_subscriptions()
        assert "emitter.started" in phantom
        assert "emitter.completed" in phantom


# =============================================================================
# Test Full Check Method
# =============================================================================


class TestCheck:
    """Tests for the comprehensive check() method."""

    def test_check_valid_system(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)
        runner.register(SubscriberMachine)

        report = runner.check()

        assert report.is_valid is True
        assert "EmitterMachine" in report.machines_analyzed
        assert "SubscriberMachine" in report.machines_analyzed
        assert report.total_states == 6  # 3 + 3
        assert report.total_transitions == 4  # 2 + 2
        assert report.unreachable_states == {}
        assert report.terminal_states == {}
        assert report.dead_letters == []
        assert report.phantom_subscriptions == []

    def test_check_system_with_unreachable(self):
        runner = SimulationRunner()
        runner.register(MachineWithUnreachableState)

        report = runner.check()

        assert report.is_valid is False
        assert "MachineWithUnreachableState" in report.unreachable_states
        assert "unreachable" in report.unreachable_states["MachineWithUnreachableState"]

    def test_check_system_with_deadlock(self):
        runner = SimulationRunner()
        runner.register(MachineWithDeadlock)

        report = runner.check()

        assert report.is_valid is False
        assert "MachineWithDeadlock" in report.terminal_states
        assert "stuck" in report.terminal_states["MachineWithDeadlock"]

    def test_check_system_with_dead_letters(self):
        runner = SimulationRunner()
        runner.register(EmitterMachine)
        # No subscriber

        report = runner.check()

        assert report.is_valid is False
        assert "emitter.started" in report.dead_letters
        assert "emitter.completed" in report.dead_letters

    def test_check_system_with_phantom_subscriptions(self):
        runner = SimulationRunner()
        runner.register(PhantomSubscriberMachine)

        report = runner.check()

        assert report.is_valid is False
        assert "nonexistent.event" in report.phantom_subscriptions

    def test_check_empty_runner(self):
        runner = SimulationRunner()

        report = runner.check()

        assert report.is_valid is True
        assert report.machines_analyzed == []
        assert report.total_states == 0
        assert report.total_transitions == 0

    def test_check_complex_valid_system(self):
        """Test a more complex but valid system."""
        runner = SimulationRunner()
        runner.register(CyclicMachine)
        runner.register(MultiPathMachine)

        report = runner.check()

        # These machines don't emit or subscribe, so there are no event issues
        # All states are reachable and can reach final
        assert report.is_valid is True
        assert report.total_states == 9  # 4 + 5
        # CyclicMachine: 4 transitions (start, fail, rework, succeed)
        # MultiPathMachine: 4 transitions (choose, from_a, from_b, finish)
        assert report.total_transitions == 8  # 4 + 4

    def test_check_multiple_issues(self):
        """Test a system with multiple structural issues."""
        runner = SimulationRunner()
        runner.register(MachineWithUnreachableState)
        runner.register(MachineWithDeadlock)
        runner.register(PhantomSubscriberMachine)

        report = runner.check()

        assert report.is_valid is False
        assert len(report.machines_analyzed) == 3
        assert "unreachable" in report.unreachable_states.get("MachineWithUnreachableState", [])
        assert "stuck" in report.terminal_states.get("MachineWithDeadlock", [])
        assert "nonexistent.event" in report.phantom_subscriptions


# =============================================================================
# Test with OrderLifecycle (Real Machine)
# =============================================================================


class TestWithOrderLifecycle:
    """Tests using the actual OrderLifecycle reference machine."""

    def test_order_lifecycle_structure(self):
        from machines.order_lifecycle import OrderLifecycle

        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        # All states should be reachable
        reachable = runner.reachability(OrderLifecycle)
        assert reachable == {"created", "validated", "fulfilled", "completed", "cancelled"}

        # No dead states
        dead = runner.dead_states(OrderLifecycle)
        assert dead == set()

        # No termination issues
        issues = runner.termination_issues(OrderLifecycle)
        assert issues == set()

    def test_order_lifecycle_emits(self):
        from machines.order_lifecycle import OrderLifecycle

        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        emitted = runner._find_emitted_events(OrderLifecycle)
        assert emitted == {
            "order.validated",
            "order.fulfilled",
            "order.completed",
            "order.cancelled",
        }

    def test_order_lifecycle_dead_letters(self):
        """OrderLifecycle emits events but has no subscribers - dead letters."""
        from machines.order_lifecycle import OrderLifecycle

        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        # Without other machines subscribing, all emitted events are dead letters
        dead = runner.dead_letters()
        assert "order.validated" in dead
        assert "order.fulfilled" in dead
        assert "order.completed" in dead
        assert "order.cancelled" in dead

    def test_order_lifecycle_check(self):
        from machines.order_lifecycle import OrderLifecycle

        runner = SimulationRunner()
        runner.register(OrderLifecycle)

        report = runner.check()

        assert "OrderLifecycle" in report.machines_analyzed
        assert report.total_states == 5
        assert report.total_transitions == 4
        assert report.unreachable_states == {}
        assert report.terminal_states == {}
        # Dead letters expected since no subscribers
        assert len(report.dead_letters) == 4
        assert report.phantom_subscriptions == []
        # Report is_valid is False due to dead letters
        assert report.is_valid is False


class TestGuardCompleteness:
    """Test detection of guarded transitions with no fallback branch."""

    def test_no_issues_for_deterministic_transitions(self):
        class M(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            go = a.to(b)

        runner = SimulationRunner()
        runner.register(M)
        assert runner.guard_completeness_issues(M) == []

    def test_no_issues_when_last_branch_has_no_guard(self):
        """The OrderLifecycle pattern: explicit guard on first branch, fallback on second."""
        class M(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            c = State(final=True)
            choose = a.to(b) | a.to(c)

            def guard_choose_to_b(self, **kwargs):
                return kwargs.get("ok", False)
            # No guard_choose_to_c → fallback

        runner = SimulationRunner()
        runner.register(M)
        assert runner.guard_completeness_issues(M) == []

    def test_detects_missing_fallback(self):
        """When every branch has an explicit guard and all may return False."""
        class M(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            c = State(final=True)
            choose = a.to(b) | a.to(c)

            def guard_choose_to_b(self, **kwargs):
                return False

            def guard_choose_to_c(self, **kwargs):
                return False

        runner = SimulationRunner()
        runner.register(M)
        gaps = runner.guard_completeness_issues(M)
        assert ("choose", "a") in gaps

    def test_check_surfaces_guard_gaps(self):
        """check() populates guard_completeness_issues and flips is_valid."""
        class M(StateMachine):
            a = State(initial=True)
            b = State(final=True)
            c = State(final=True)
            choose = a.to(b) | a.to(c)

            def guard_choose_to_b(self, **kwargs):
                return False

            def guard_choose_to_c(self, **kwargs):
                return False

        runner = SimulationRunner()
        runner.register(M)
        report = runner.check()
        assert report.guard_completeness_issues["M"] == [("choose", "a")]
        assert report.is_valid is False

    def test_order_lifecycle_has_no_guard_gaps(self):
        """The reference machine should pass guard-completeness."""
        from machines.order_lifecycle import OrderLifecycle
        runner = SimulationRunner()
        runner.register(OrderLifecycle)
        assert runner.guard_completeness_issues(OrderLifecycle) == []
