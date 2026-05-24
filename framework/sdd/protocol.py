"""State machine protocol for simulation-driven development."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from sdd.events import Event, EventBus


@dataclass(frozen=True)
class TransitionResult:
    """Result of attempting to fire a transition."""

    success: bool
    source: str
    target: str
    transition: str
    failure_reason: str | None = None
    events_emitted: list["Event"] = field(default_factory=list)


@dataclass(frozen=True)
class TransitionRecord:
    """Historical record of a fired transition."""

    transition: str
    source: str
    target: str
    kwargs: dict


class TransitionBranch:
    """A single source -> target branch of a transition."""

    def __init__(self, source: "State", target: "State") -> None:
        # Store State references, not names (names resolved later by metaclass)
        self._source_state = source
        self._target_state = target
        self.source: str = ""  # Resolved by metaclass
        self.target: str = ""  # Resolved by metaclass

    def _resolve_names(self) -> None:
        """Resolve state names from State references."""
        self.source = self._source_state._name
        self.target = self._target_state._name

    def __repr__(self) -> str:
        return f"TransitionBranch({self.source!r} -> {self.target!r})"


class Transition:
    """Descriptor representing a transition between states."""

    def __init__(self, branches: list[TransitionBranch]) -> None:
        self.branches = branches
        self.name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __or__(self, other: Transition) -> Transition:
        """Combine transitions with | operator."""
        return Transition(self.branches + other.branches)

    def _resolve_names(self) -> None:
        """Resolve all branch state names."""
        for branch in self.branches:
            branch._resolve_names()

    def get_branches_from(self, source: str) -> list[TransitionBranch]:
        """Get all branches that can fire from the given source state."""
        return [b for b in self.branches if b.source == source]

    def get_all_sources(self) -> set[str]:
        """Get all source states for this transition."""
        return {b.source for b in self.branches}

    def __repr__(self) -> str:
        return f"Transition({self.name!r}, {self.branches})"


class StateGroup:
    """A group of states that can be used as transition sources."""

    def __init__(self, states: list["State"]) -> None:
        # Store State references, not names
        self._states = states

    def __or__(self, other: "State | StateGroup") -> "StateGroup":
        """Combine state groups with | operator."""
        if isinstance(other, State):
            return StateGroup(self._states + [other])
        return StateGroup(self._states + other._states)

    def to(self, target: "State") -> Transition:
        """Create a transition from all states in this group to a target."""
        branches = [TransitionBranch(src, target) for src in self._states]
        return Transition(branches)


class State:
    """Descriptor for declaring states in a state machine."""

    def __init__(self, *, initial: bool = False, final: bool = False) -> None:
        self.initial = initial
        self.final = final
        self._name: str = ""
        self._owner: type | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        self._owner = owner

    def __get__(self, obj: Any, objtype: type | None = None) -> str:
        """Return the state name when accessed."""
        return self._name

    def __or__(self, other: State | StateGroup) -> StateGroup:
        """Combine states with | operator for multi-source transitions."""
        if isinstance(other, State):
            return StateGroup([self, other])
        return StateGroup([self] + other._states)

    def to(self, target: State) -> Transition:
        """Create a transition from this state to the target state."""
        return Transition([TransitionBranch(self, target)])

    def __repr__(self) -> str:
        flags = []
        if self.initial:
            flags.append("initial")
        if self.final:
            flags.append("final")
        flag_str = f"({', '.join(flags)})" if flags else ""
        return f"State({self._name!r}{flag_str})"


class StateMachineMeta(type):
    """Metaclass that collects state and transition declarations."""

    def __new__(
        mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any]
    ) -> StateMachineMeta:
        cls = super().__new__(mcs, name, bases, namespace)

        # Skip processing for the base StateMachine class
        if name == "StateMachine" and not bases:
            return cls

        # Collect states from this class and all base classes
        states: dict[str, State] = {}
        transitions: dict[str, Transition] = {}

        # Walk the MRO to collect inherited states/transitions
        for base in reversed(cls.__mro__):
            if base is object:
                continue
            for attr_name, attr_value in vars(base).items():
                if isinstance(attr_value, State):
                    states[attr_name] = attr_value
                elif isinstance(attr_value, Transition):
                    transitions[attr_name] = attr_value

        cls._states = states
        cls._transitions = transitions

        # Resolve state names in transitions (names are now set by __set_name__)
        for transition in transitions.values():
            transition._resolve_names()

        # Discover @timeout-decorated methods and resolve their transition names
        timeouts: list[tuple[str, str, float, Any]] = []
        for attr_name in dir(cls):
            method = getattr(cls, attr_name, None)
            decl = getattr(method, "_sdd_timeout", None)
            if decl is None:
                continue
            # If the decorator was given a Transition descriptor, look up its name now
            transition_obj = getattr(method, "_sdd_timeout_transition_obj", None)
            if transition_obj is not None:
                decl.transition_name = transition_obj.name
            # If no source_state was specified, infer from the transition's branches
            source_state = decl.source_state
            if source_state is None:
                transition = transitions.get(decl.transition_name)
                if transition and transition.branches:
                    source_state = transition.branches[0].source
            if not decl.transition_name or source_state is None:
                continue
            timeouts.append((source_state, decl.transition_name, decl.delay_seconds, attr_name))
        cls._timeouts = timeouts  # list of (source_state, transition_name, delay, method_name)

        # Validate: exactly one initial state
        initial_states = [s for s in states.values() if s.initial]
        if len(initial_states) == 0 and states:  # Allow empty for base class
            raise TypeError(f"{name} must have exactly one initial state")
        if len(initial_states) > 1:
            raise TypeError(f"{name} has multiple initial states")

        # Validate: at least one final state
        final_states = [s for s in states.values() if s.final]
        if len(final_states) == 0 and states:
            raise TypeError(f"{name} must have at least one final state")

        return cls


class StateMachine(metaclass=StateMachineMeta):
    """Base class for all state machines."""

    _states: ClassVar[dict[str, State]] = {}
    _transitions: ClassVar[dict[str, Transition]] = {}
    _timeouts: ClassVar[list[tuple[str, str, float, str]]] = []

    def __init__(
        self,
        context: dict | None = None,
        event_bus: "EventBus | None" = None,
        instance_id: str = "",
    ) -> None:
        self._context = dict(context) if context else {}
        self._event_bus = event_bus
        self._instance_id = instance_id
        self._history: list[TransitionRecord] = []
        self._pending_events: list["Event"] = []

        # Find and set initial state
        for name, state in self._states.items():
            if state.initial:
                self._current_state = name
                break

    @property
    def context(self) -> dict:
        """Return a copy of the context to prevent external mutation."""
        return dict(self._context)

    @property
    def current_state(self) -> str:
        """Name of the current state."""
        return self._current_state

    @property
    def is_final(self) -> bool:
        """Whether the current state is a final state."""
        state = self._states.get(self._current_state)
        return state.final if state else False

    @property
    def available_transitions(self) -> list[str]:
        """Transitions that can fire from the current state."""
        if self.is_final:
            return []
        result = []
        for name, transition in self._transitions.items():
            if self._current_state in transition.get_all_sources():
                result.append(name)
        return result

    @property
    def all_states(self) -> list[str]:
        """All declared states."""
        return list(self._states.keys())

    @property
    def all_transitions(self) -> list[dict]:
        """All declared transitions with source/target info."""
        result = []
        for name, transition in self._transitions.items():
            for branch in transition.branches:
                result.append({
                    "name": name,
                    "source": branch.source,
                    "target": branch.target,
                })
        return result

    @property
    def history(self) -> list[TransitionRecord]:
        """Ordered log of all transitions fired."""
        return list(self._history)

    def fire(self, transition_name: str, **kwargs: Any) -> TransitionResult:
        """Attempt to fire a transition."""
        from sdd.events import Event

        source = self._current_state

        # Check if transition exists
        transition = self._transitions.get(transition_name)
        if transition is None:
            return TransitionResult(
                success=False,
                source=source,
                target=source,
                transition=transition_name,
                failure_reason="unknown_transition",
            )

        # Check if transition can fire from current state
        branches = transition.get_branches_from(source)
        if not branches:
            return TransitionResult(
                success=False,
                source=source,
                target=source,
                transition=transition_name,
                failure_reason="invalid_source_state",
            )

        # Merge kwargs into context before evaluating guards
        self._context.update(kwargs)

        # Find the first branch whose guard passes
        target = None
        for branch in branches:
            guard_name = f"guard_{transition_name}_to_{branch.target}"
            guard = getattr(self, guard_name, None)
            if guard is None:
                # No guard defined = always passes
                target = branch.target
                break
            # Pass a read-only copy of context to guard
            if guard(**kwargs):
                target = branch.target
                break

        if target is None:
            return TransitionResult(
                success=False,
                source=source,
                target=source,
                transition=transition_name,
                failure_reason="no_guard_passed",
            )

        # Clear pending events before executing side effects
        self._pending_events = []

        # Execute on_exit for source state
        exit_method = getattr(self, f"on_exit_{source}", None)
        if exit_method:
            exit_method()

        # Execute on_transition hook
        transition_method = getattr(self, f"on_transition_{transition_name}", None)
        if transition_method:
            transition_method()

        # Update state
        self._current_state = target

        # Execute on_enter for target state
        enter_method = getattr(self, f"on_enter_{target}", None)
        if enter_method:
            enter_method()

        # Record in history
        self._history.append(TransitionRecord(
            transition=transition_name,
            source=source,
            target=target,
            kwargs=dict(kwargs),
        ))

        # Collect emitted events and deliver them
        events_emitted = list(self._pending_events)
        if self._event_bus:
            for event in events_emitted:
                self._event_bus.emit(event)
        self._pending_events = []

        return TransitionResult(
            success=True,
            source=source,
            target=target,
            transition=transition_name,
            events_emitted=events_emitted,
        )

    def emit(self, name: str, payload: dict) -> None:
        """Emit an event. Called from side effects."""
        from sdd.events import Event
        import time

        event = Event(
            name=name,
            payload=payload,
            source_machine=self.__class__.__name__,
            source_instance=self._instance_id,
            timestamp=time.monotonic(),
            correlation_id=self._context.get("correlation_id", ""),
        )
        self._pending_events.append(event)

    def snapshot(self) -> dict:
        """Serialize machine state for persistence."""
        return {
            "state": self._current_state,
            "context": dict(self._context),
            "history": [
                {
                    "transition": r.transition,
                    "source": r.source,
                    "target": r.target,
                    "kwargs": r.kwargs,
                }
                for r in self._history
            ],
        }

    @classmethod
    def restore(
        cls,
        snapshot: dict,
        event_bus: "EventBus | None" = None,
        instance_id: str = "",
    ) -> "StateMachine":
        """Reconstitute a machine from a snapshot."""
        machine = cls(
            context=snapshot.get("context", {}),
            event_bus=event_bus,
            instance_id=instance_id,
        )
        machine._current_state = snapshot["state"]
        machine._history = [
            TransitionRecord(
                transition=r["transition"],
                source=r["source"],
                target=r["target"],
                kwargs=r["kwargs"],
            )
            for r in snapshot.get("history", [])
        ]
        return machine

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        """Map event names to transition names. Override in subclasses."""
        return {}
