"""Simulation runner for operating state machines and routing events."""

from __future__ import annotations

import ast
import inspect
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from sdd.events import Event, EventBus, EventLog, SchemaValidationError
from sdd.protocol import StateMachine, TransitionRecord, TransitionResult

if TYPE_CHECKING:
    pass


@dataclass
class StructuralReport:
    """Results of structural analysis on registered machines."""

    machines_analyzed: list[str] = field(default_factory=list)
    total_states: int = 0
    total_transitions: int = 0
    unreachable_states: dict[str, list[str]] = field(default_factory=dict)
    terminal_states: dict[str, list[str]] = field(default_factory=dict)
    dead_letters: list[str] = field(default_factory=list)
    phantom_subscriptions: list[str] = field(default_factory=list)
    # machine -> list[(transition, source_state)] for guarded transitions that
    # could fail with no_guard_passed because every branch has an explicit guard
    # and none is a guaranteed fallback.
    guard_completeness_issues: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        """True if no structural issues were found."""
        has_unreachable = any(states for states in self.unreachable_states.values())
        has_terminal = any(states for states in self.terminal_states.values())
        has_guard_gaps = any(items for items in self.guard_completeness_issues.values())
        return (
            not has_unreachable
            and not has_terminal
            and not self.dead_letters
            and not self.phantom_subscriptions
            and not has_guard_gaps
        )


@dataclass
class StepError:
    """Records a failure during step execution."""

    step_index: int  # Position in cascade (0 = initial fire)
    machine: str  # Machine class name
    instance: str  # Instance ID
    transition: str  # Transition that failed
    error_type: str  # "unknown_transition" | "invalid_source_state" | "no_guard_passed"
    message: str  # Human-readable description
    machine_state_at_error: str  # State when error occurred


@dataclass
class RoutingFailure:
    """Records a failure to route an event to a subscribing machine instance."""

    event_name: str
    event_payload_keys: list[str]
    source_machine: str
    source_instance: str
    target_machine: str
    target_transition: str
    reason: str  # "no_resolver_registered" | "resolver_returned_none" | "resolver_raised"
    message: str


@dataclass
class StepResult:
    """Result of executing a step (fire) and its cascading effects."""

    trigger: str  # What initiated this step (e.g., "fire:validate")
    transitions_fired: list[TransitionRecord] = field(default_factory=list)
    events_emitted: list[Event] = field(default_factory=list)
    cascade_depth: int = 0
    errors: list[StepError] = field(default_factory=list)
    routing_failures: list[RoutingFailure] = field(default_factory=list)


class SimulationRunner:
    """
    Environment for operating state machines during simulation.

    Manages machine instances, routes events, executes transitions,
    and exposes system state for inspection.
    """

    def __init__(self) -> None:
        self._event_bus = EventBus()
        self._machine_classes: dict[type, type[StateMachine]] = {}
        self._resolvers: dict[type, Callable[[Event], str]] = {}
        self._instances: dict[tuple[type, str], StateMachine] = {}

        # Track cascade state during fire execution
        self._current_step_result: StepResult | None = None
        self._cascade_depth: int = 0
        self._step_index: int = 0

        # Routing failures observed outside any active step
        # (e.g., events emitted by adapter code or test helpers)
        self._unscoped_routing_failures: list[RoutingFailure] = []

        # Virtual clock state
        self._virtual_clock: float = 0.0
        # Map (machine_class, instance_id) -> virtual timestamp when current state was entered
        self._state_entered_at: dict[tuple[type, str], float] = {}

    @property
    def event_bus(self) -> EventBus:
        """The event bus used by this runner."""
        return self._event_bus

    @property
    def event_log(self) -> EventLog:
        """Complete ordered log of all events emitted."""
        return self._event_bus.log

    def register(self, machine_class: type[StateMachine]) -> None:
        """
        Register a machine class and wire up its subscriptions.

        Calls machine_class.subscriptions() to get event→transition mapping
        and subscribes handlers to the EventBus for each event.

        If the machine declares EVENT_SCHEMAS (class-level dict of
        event_name -> schema), those schemas are registered on the bus and
        enforced at emission time.
        """
        self._machine_classes[machine_class] = machine_class

        # Register any declared event schemas
        schemas = getattr(machine_class, "EVENT_SCHEMAS", None)
        if schemas:
            for event_name, schema in schemas.items():
                self._event_bus.register_schema(event_name, schema)

        # Wire up subscriptions
        subscriptions = machine_class.subscriptions()
        for event_name, transition_name in subscriptions.items():
            self._subscribe_transition(machine_class, event_name, transition_name)

    def _subscribe_transition(
        self,
        machine_class: type[StateMachine],
        event_name: str,
        transition_name: str,
    ) -> None:
        """Subscribe to an event that triggers a transition on a machine."""

        def handler(event: Event) -> None:
            # Resolve instance ID from event payload
            resolver = self._resolvers.get(machine_class)
            if resolver is None:
                self._record_routing_failure(
                    event=event,
                    machine_class=machine_class,
                    transition=transition_name,
                    reason="no_resolver_registered",
                    message=(
                        f"No resolver registered for {machine_class.__name__}; "
                        f"cannot route event '{event.name}'."
                    ),
                )
                return

            try:
                resolved_instance_id = resolver(event)
            except Exception as exc:
                self._record_routing_failure(
                    event=event,
                    machine_class=machine_class,
                    transition=transition_name,
                    reason="resolver_raised",
                    message=(
                        f"Resolver for {machine_class.__name__} raised "
                        f"{type(exc).__name__} on event '{event.name}': {exc}"
                    ),
                )
                return

            if resolved_instance_id is None:
                self._record_routing_failure(
                    event=event,
                    machine_class=machine_class,
                    transition=transition_name,
                    reason="resolver_returned_none",
                    message=(
                        f"Resolver for {machine_class.__name__} returned None on event "
                        f"'{event.name}' (payload keys: {sorted(event.payload.keys())}); "
                        f"cannot route to a {machine_class.__name__} instance."
                    ),
                )
                return

            # Get or create instance
            instance = self.get(machine_class, resolved_instance_id)
            if instance is None:
                # Implicit creation: seed context from event payload
                instance = self.create(machine_class, resolved_instance_id, dict(event.payload))

            # Fire the transition with event payload as kwargs
            self._fire_internal(
                machine_class, resolved_instance_id, transition_name, **event.payload
            )

        self._event_bus.subscribe(event_name, handler)

    def _record_routing_failure(
        self,
        event: Event,
        machine_class: type[StateMachine],
        transition: str,
        reason: str,
        message: str,
    ) -> None:
        """Record a routing failure on the active StepResult (or globally)."""
        failure = RoutingFailure(
            event_name=event.name,
            event_payload_keys=sorted(event.payload.keys()),
            source_machine=event.source_machine,
            source_instance=event.source_instance,
            target_machine=machine_class.__name__,
            target_transition=transition,
            reason=reason,
            message=message,
        )
        if self._current_step_result is not None:
            self._current_step_result.routing_failures.append(failure)
        else:
            self._unscoped_routing_failures.append(failure)

    @property
    def routing_failures(self) -> list[RoutingFailure]:
        """Routing failures recorded outside any active step."""
        return list(self._unscoped_routing_failures)

    def register_resolver(
        self,
        machine_class: type[StateMachine],
        resolver: Callable[[Event], str],
    ) -> None:
        """
        Set how to extract instance ID from event payload.

        Example:
            runner.register_resolver(OrderLifecycle, lambda e: e.payload["order_id"])
        """
        self._resolvers[machine_class] = resolver

    def create(
        self,
        machine_class: type[StateMachine],
        instance_id: str,
        context: dict | None = None,
    ) -> StateMachine:
        """
        Create a machine instance with the given context.

        Registers the instance in the (class, id) keyed registry.
        """
        instance = machine_class(
            context=context,
            event_bus=self._event_bus,
            instance_id=instance_id,
        )
        self._instances[(machine_class, instance_id)] = instance
        # Initial state entered at the current virtual time
        self._state_entered_at[(machine_class, instance_id)] = self._virtual_clock
        return instance

    def get(
        self,
        machine_class: type[StateMachine],
        instance_id: str,
    ) -> StateMachine | None:
        """Retrieve an existing instance or return None."""
        return self._instances.get((machine_class, instance_id))

    def fire(
        self,
        instance_id: str,
        machine_class: type[StateMachine],
        transition: str,
        **kwargs,
    ) -> StepResult:
        """
        Fire a transition on a machine instance and handle the full cascade.

        Returns a StepResult capturing everything that happened:
        - All transitions fired (including from event cascades)
        - All events emitted
        - Any errors encountered
        - The cascade depth
        """
        # Initialize step tracking
        self._current_step_result = StepResult(trigger=f"fire:{transition}")
        self._cascade_depth = 0
        self._step_index = 0

        # Capture event log length before firing
        events_before = len(self._event_bus._log)

        # Fire the initial transition
        self._fire_internal(machine_class, instance_id, transition, **kwargs)

        # Collect all events emitted during this step
        events_after = len(self._event_bus._log)
        if events_after > events_before:
            self._current_step_result.events_emitted = list(
                self._event_bus._log[events_before:events_after]
            )

        self._current_step_result.cascade_depth = self._cascade_depth

        result = self._current_step_result
        self._current_step_result = None
        return result

    def emit_event(
        self,
        name: str,
        payload: dict | None = None,
        correlation_id: str = "",
    ) -> StepResult:
        """
        Inject a synthetic event onto the bus as if an adapter had emitted it.

        The event is delivered through normal subscription routing — subscriber
        resolvers fire, target instances are looked up or implicit-created, and
        downstream transitions cascade. The returned StepResult captures the
        full cascade in the same shape as ``fire()``: transitions fired, events
        emitted, errors, and routing failures.

        Schema validation runs first. A payload that does not match a
        registered ``EVENT_SCHEMAS`` entry is recorded as a
        ``schema_validation_failed`` StepError rather than crashing the
        scenario — and the synthetic event does not enter the log.

        The synthetic event's source is marked ``source_machine="_scenario"``
        so downstream consumers can distinguish it from real machine emissions.
        """
        self._current_step_result = StepResult(trigger=f"emit:{name}")
        self._cascade_depth = 0
        self._step_index = 0

        events_before = len(self._event_bus._log)

        event = Event(
            name=name,
            payload=payload or {},
            source_machine="_scenario",
            source_instance="",
            correlation_id=correlation_id,
        )

        try:
            self._event_bus.emit(event)
        except SchemaValidationError as exc:
            self._current_step_result.errors.append(
                StepError(
                    step_index=self._step_index,
                    machine="_scenario",
                    instance="",
                    transition=f"emit:{name}",
                    error_type="schema_validation_failed",
                    message=str(exc),
                    machine_state_at_error="",
                )
            )

        events_after = len(self._event_bus._log)
        if events_after > events_before:
            self._current_step_result.events_emitted = list(
                self._event_bus._log[events_before:events_after]
            )
        self._current_step_result.cascade_depth = self._cascade_depth

        result = self._current_step_result
        self._current_step_result = None
        return result

    def _fire_internal(
        self,
        machine_class: type[StateMachine],
        target_instance_id: str,
        transition: str,
        **kwargs,
    ) -> TransitionResult | None:
        """
        Internal method to fire a transition, recording results.

        Does not crash on errors - records them in the current StepResult.
        """
        instance = self.get(machine_class, target_instance_id)
        if instance is None:
            if self._current_step_result:
                self._current_step_result.errors.append(
                    StepError(
                        step_index=self._step_index,
                        machine=machine_class.__name__,
                        instance=target_instance_id,
                        transition=transition,
                        error_type="instance_not_found",
                        message=f"No instance found for {machine_class.__name__}('{target_instance_id}')",
                        machine_state_at_error="",
                    )
                )
            return None

        # Track cascade depth
        self._cascade_depth += 1
        self._step_index += 1

        # Fire the transition. Schema-validation errors from event emission
        # propagate up here; we catch them and record as StepErrors so the
        # scenario doesn't crash mid-cascade.
        try:
            result = instance.fire(transition, **kwargs)
        except SchemaValidationError as exc:
            if self._current_step_result is not None:
                self._current_step_result.errors.append(
                    StepError(
                        step_index=self._step_index,
                        machine=machine_class.__name__,
                        instance=target_instance_id,
                        transition=transition,
                        error_type="schema_validation_failed",
                        message=str(exc),
                        machine_state_at_error=instance.current_state,
                    )
                )
            return None

        if result.success:
            # Record state-entry timestamp for timeout tracking
            self._state_entered_at[(machine_class, target_instance_id)] = self._virtual_clock

            if self._current_step_result:
                # Record the transition
                self._current_step_result.transitions_fired.append(
                    TransitionRecord(
                        transition=result.transition,
                        source=result.source,
                        target=result.target,
                        kwargs=dict(kwargs),
                    )
                )
        else:
            # Record the error
            if self._current_step_result:
                self._current_step_result.errors.append(
                    StepError(
                        step_index=self._step_index,
                        machine=machine_class.__name__,
                        instance=target_instance_id,
                        transition=transition,
                        error_type=result.failure_reason or "unknown",
                        message=self._format_error_message(
                            result.failure_reason, machine_class, instance, transition
                        ),
                        machine_state_at_error=instance.current_state,
                    )
                )

        return result

    def _format_error_message(
        self,
        failure_reason: str | None,
        machine_class: type,
        instance: StateMachine,
        transition: str,
    ) -> str:
        """Format a human-readable error message."""
        if failure_reason == "unknown_transition":
            return f"Transition '{transition}' does not exist on {machine_class.__name__}"
        elif failure_reason == "invalid_source_state":
            return (
                f"Transition '{transition}' cannot fire from state '{instance.current_state}' "
                f"on {machine_class.__name__}"
            )
        elif failure_reason == "no_guard_passed":
            return (
                f"No guard passed for transition '{transition}' from state "
                f"'{instance.current_state}' on {machine_class.__name__}"
            )
        return f"Unknown error firing '{transition}' on {machine_class.__name__}"

    def reset(self) -> None:
        """
        Destroy all instances and clear the event log.

        Retains machine class registrations and resolvers.
        """
        self._instances.clear()
        self._unscoped_routing_failures.clear()
        self._virtual_clock = 0.0
        self._state_entered_at.clear()

        # Clear the event bus log but keep subscriptions
        self._event_bus._log.clear()
        self._event_bus._delivery_queue.clear()
        self._event_bus._cascade_depth = 0
        self._event_bus._delivering = False

    def machine_states(self) -> dict[tuple[type, str], str]:
        """
        Get the current state of all machine instances.

        Returns:
            Dict mapping (MachineClass, instance_id) to current state name.
        """
        return {key: instance.current_state for key, instance in self._instances.items()}

    def active_instances(self) -> list[tuple[type, str]]:
        """
        Get all non-final machine instances.

        Returns:
            List of (MachineClass, instance_id) tuples.
        """
        return [
            key for key, instance in self._instances.items() if not instance.is_final
        ]

    def final_instances(self) -> list[tuple[type, str]]:
        """
        Get all machine instances in a final state.

        Returns:
            List of (MachineClass, instance_id) tuples.
        """
        return [key for key, instance in self._instances.items() if instance.is_final]

    def get_machine_class(self, name: str) -> type[StateMachine] | None:
        """
        Look up a registered machine class by name.

        Args:
            name: The class name (e.g., "OrderLifecycle").

        Returns:
            The machine class if found, None otherwise.
        """
        for cls in self._machine_classes:
            if cls.__name__ == name:
                return cls
        return None

    # =========================================================================
    # Structural Validation Methods
    # =========================================================================

    def reachability(self, machine_class: type[StateMachine]) -> set[str]:
        """
        Compute reachable states from the initial state via BFS.

        Args:
            machine_class: The machine class to analyze.

        Returns:
            Set of state names reachable from the initial state.
        """
        # Find initial state
        initial_state: str | None = None
        for name, state in machine_class._states.items():
            if state.initial:
                initial_state = name
                break

        if initial_state is None:
            return set()

        # BFS from initial state
        visited: set[str] = set()
        queue: deque[str] = deque([initial_state])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # Find all transitions that can fire from current state
            for transition in machine_class._transitions.values():
                for branch in transition.branches:
                    if branch.source == current:
                        if branch.target not in visited:
                            queue.append(branch.target)

        return visited

    def dead_states(self, machine_class: type[StateMachine]) -> set[str]:
        """
        Find states that are declared but not reachable from the initial state.

        Args:
            machine_class: The machine class to analyze.

        Returns:
            Set of unreachable state names.
        """
        all_states = set(machine_class._states.keys())
        reachable = self.reachability(machine_class)
        return all_states - reachable

    def termination_issues(self, machine_class: type[StateMachine]) -> set[str]:
        """
        Find non-final states with no path to any final state.

        A state with no path to a final state is a potential deadlock.

        Args:
            machine_class: The machine class to analyze.

        Returns:
            Set of state names that cannot reach any final state.
        """
        # Find all final states
        final_states: set[str] = set()
        for name, state in machine_class._states.items():
            if state.final:
                final_states.add(name)

        if not final_states:
            # No final states means all non-final states are problematic
            return set(machine_class._states.keys())

        # Build reverse graph: for each state, which states can reach it
        # We'll do BFS backwards from final states
        can_reach_final: set[str] = set()
        queue: deque[str] = deque(final_states)

        while queue:
            current = queue.popleft()
            if current in can_reach_final:
                continue
            can_reach_final.add(current)

            # Find all states that can transition TO current
            for transition in machine_class._transitions.values():
                for branch in transition.branches:
                    if branch.target == current:
                        if branch.source not in can_reach_final:
                            queue.append(branch.source)

        # Non-final states not in can_reach_final are problematic
        problematic: set[str] = set()
        for name, state in machine_class._states.items():
            if not state.final and name not in can_reach_final:
                problematic.add(name)

        return problematic

    def guard_completeness_issues(
        self, machine_class: type[StateMachine]
    ) -> list[tuple[str, str]]:
        """
        Find guarded transitions where every branch from a given source state has
        an explicit guard, with no fallback branch — i.e. transitions that could
        fail with `no_guard_passed` if no guard returns True.

        A branch is considered safe (a guaranteed fallback) when no `guard_*`
        method exists for it on the class. Methods are checked via getattr so
        inherited guards also count.

        Returns:
            List of (transition_name, source_state) pairs that lack a fallback.
        """
        issues: list[tuple[str, str]] = []
        for transition_name, transition in machine_class._transitions.items():
            # Group branches by source state — guard completeness is per source
            sources: dict[str, list[str]] = {}
            for branch in transition.branches:
                sources.setdefault(branch.source, []).append(branch.target)

            for source_state, targets in sources.items():
                if len(targets) <= 1:
                    # Deterministic transition (one target from this source) —
                    # if its guard fails, that's intentional rejection, not a gap
                    continue
                # Guarded: check whether at least one branch has no explicit guard
                has_fallback = False
                for target in targets:
                    guard_name = f"guard_{transition_name}_to_{target}"
                    if getattr(machine_class, guard_name, None) is None:
                        has_fallback = True
                        break
                if not has_fallback:
                    issues.append((transition_name, source_state))
        return issues

    def _find_emitted_events(self, machine_class: type[StateMachine]) -> set[str]:
        """
        Find all events emitted by a machine class by inspecting its methods.

        Uses AST parsing to find self.emit() calls in on_enter_*, on_exit_*,
        and on_transition_* methods.

        Args:
            machine_class: The machine class to analyze.

        Returns:
            Set of event names emitted by the machine.
        """
        import textwrap

        emitted_events: set[str] = set()

        # Methods that can emit events
        method_prefixes = ("on_enter_", "on_exit_", "on_transition_")

        for attr_name in dir(machine_class):
            if not any(attr_name.startswith(p) for p in method_prefixes):
                continue

            method = getattr(machine_class, attr_name, None)
            if method is None or not callable(method):
                continue

            # Get the source code and parse it
            try:
                source = inspect.getsource(method)
                # Dedent the source to handle class method indentation
                source = textwrap.dedent(source)
                tree = ast.parse(source)
            except (OSError, TypeError, SyntaxError):
                # Can't get source or parse it
                continue

            # Walk the AST looking for self.emit() calls
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check if it's a method call on self
                    if isinstance(node.func, ast.Attribute):
                        if node.func.attr == "emit":
                            # Check if called on self
                            if isinstance(node.func.value, ast.Name):
                                if node.func.value.id == "self":
                                    # Get the first argument (event name)
                                    if node.args:
                                        first_arg = node.args[0]
                                        if isinstance(first_arg, ast.Constant):
                                            if isinstance(first_arg.value, str):
                                                emitted_events.add(first_arg.value)

        return emitted_events

    def dead_letters(self) -> list[str]:
        """
        Find events emitted by machines but not subscribed to by any machine.

        Returns:
            List of event names that are emitted but never subscribed to.
        """
        # Collect all emitted events from all registered machines
        all_emitted: set[str] = set()
        for machine_class in self._machine_classes:
            all_emitted.update(self._find_emitted_events(machine_class))

        # Collect all subscribed events from all registered machines
        all_subscribed: set[str] = set()
        for machine_class in self._machine_classes:
            subscriptions = machine_class.subscriptions()
            all_subscribed.update(subscriptions.keys())

        # Dead letters are emitted but not subscribed
        return sorted(all_emitted - all_subscribed)

    def phantom_subscriptions(self) -> list[str]:
        """
        Find events subscribed to by machines but not emitted by any machine.

        Returns:
            List of event names that are subscribed to but never emitted.
        """
        # Collect all emitted events from all registered machines
        all_emitted: set[str] = set()
        for machine_class in self._machine_classes:
            all_emitted.update(self._find_emitted_events(machine_class))

        # Collect all subscribed events from all registered machines
        all_subscribed: set[str] = set()
        for machine_class in self._machine_classes:
            subscriptions = machine_class.subscriptions()
            all_subscribed.update(subscriptions.keys())

        # Phantom subscriptions are subscribed but not emitted
        return sorted(all_subscribed - all_emitted)

    # =========================================================================
    # Virtual Clock
    # =========================================================================

    @property
    def virtual_clock(self) -> float:
        """Current virtual time in seconds since reset."""
        return self._virtual_clock

    def advance_time(
        self,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> StepResult:
        """
        Advance the virtual clock and fire any due timeout transitions.

        Returns a StepResult capturing transitions fired and events emitted
        during this advancement. If multiple timeouts come due, they are
        fired in deadline order.
        """
        delta = seconds + 60 * minutes + 3600 * hours + 86400 * days
        if delta <= 0:
            return StepResult(trigger=f"advance_time:0")

        target_time = self._virtual_clock + delta

        # Initialize step tracking
        self._current_step_result = StepResult(
            trigger=f"advance_time:{delta}s"
        )
        self._cascade_depth = 0
        self._step_index = 0
        events_before = len(self._event_bus._log)

        try:
            # Iteratively find next due timeout, advance clock to it, fire,
            # repeat until no more timeouts due before target_time.
            while True:
                due = self._next_due_timeout(target_time)
                if due is None:
                    break
                fire_time, machine_class, instance_id, transition_name, method_name = due
                self._virtual_clock = fire_time
                # Fire the timeout transition
                self._fire_internal(machine_class, instance_id, transition_name)
                # Run the decorated method (post-fire hook) if present
                instance = self.get(machine_class, instance_id)
                if instance is not None:
                    hook = getattr(instance, method_name, None)
                    if callable(hook):
                        hook()

            self._virtual_clock = target_time

            events_after = len(self._event_bus._log)
            if events_after > events_before:
                self._current_step_result.events_emitted = list(
                    self._event_bus._log[events_before:events_after]
                )
            self._current_step_result.cascade_depth = self._cascade_depth
            return self._current_step_result
        finally:
            self._current_step_result = None

    def _next_due_timeout(self, target_time: float):
        """
        Find the earliest (instance, timeout) pair whose deadline is ≤ target_time
        and > the current virtual clock. Returns None if nothing is due.
        """
        earliest = None
        for (cls, iid), entered_at in list(self._state_entered_at.items()):
            instance = self._instances.get((cls, iid))
            if instance is None or instance.is_final:
                continue
            current_state = instance.current_state
            for source_state, trans_name, delay, method_name in cls._timeouts:
                if source_state != current_state:
                    continue
                deadline = entered_at + delay
                if deadline <= self._virtual_clock:
                    # Already past — but if it hadn't fired, fire it now
                    pass
                if deadline > target_time:
                    continue
                candidate = (deadline, cls, iid, trans_name, method_name)
                if earliest is None or candidate[0] < earliest[0]:
                    earliest = candidate
        return earliest

    # =========================================================================
    # Coverage Tracking
    # =========================================================================

    def transition_coverage(
        self, scenario_results: list,
    ) -> dict[str, dict]:
        """
        Compute transition coverage per registered machine class from scenario results.

        Args:
            scenario_results: List of ScenarioResult objects. Each result's
                step_results contain TransitionRecords.

        Returns:
            Dict keyed by machine class name with:
              - "total": total declared transitions (counting each branch separately)
              - "covered": list of (transition_name, source, target) actually fired
              - "missing": list of (transition_name, source, target) never fired
              - "coverage": float in [0.0, 1.0]
        """
        # Build the universe of declared (transition, source, target) per machine
        declared: dict[str, set[tuple[str, str, str]]] = {}
        for cls in self._machine_classes:
            name = cls.__name__
            declared[name] = set()
            for tname, transition in cls._transitions.items():
                for branch in transition.branches:
                    declared[name].add((tname, branch.source, branch.target))

        # Collect fired (transition, source, target) from results
        # Note: scenario step_results store TransitionRecord, but they lack
        # machine class info. We instead inspect the runner's recorded history
        # — but the runner resets between scenarios, so we walk scenario
        # results explicitly. Each StepResult.transitions_fired has the record
        # but the source/target are sufficient given transition+source uniqueness.
        # To know which class a transition belongs to, we look it up by
        # transition name in our declared map.
        fired_by_class: dict[str, set[tuple[str, str, str]]] = {
            name: set() for name in declared
        }
        for scenario_result in scenario_results:
            for step_result in getattr(scenario_result, "step_results", []):
                for record in step_result.transitions_fired:
                    # Find which class this transition belongs to
                    for cls_name, decl_set in declared.items():
                        if any(
                            d[0] == record.transition and d[1] == record.source
                            for d in decl_set
                        ):
                            fired_by_class[cls_name].add(
                                (record.transition, record.source, record.target)
                            )

        out: dict[str, dict] = {}
        for cls_name, decl_set in declared.items():
            covered = fired_by_class[cls_name]
            missing = decl_set - covered
            total = len(decl_set)
            out[cls_name] = {
                "total": total,
                "covered": sorted(covered),
                "missing": sorted(missing),
                "coverage": (len(covered) / total) if total else 1.0,
            }
        return out

    def state_coverage(self, scenario_results: list) -> dict[str, dict]:
        """
        Compute state-entry coverage per machine class.

        A state is "covered" if any scenario transitioned into it.
        The initial state is always considered covered (machines start there).
        """
        declared: dict[str, set[str]] = {}
        initial_state_by_class: dict[str, str] = {}
        for cls in self._machine_classes:
            name = cls.__name__
            declared[name] = set(cls._states.keys())
            for sname, state in cls._states.items():
                if state.initial:
                    initial_state_by_class[name] = sname

        entered_by_class: dict[str, set[str]] = {
            name: {initial_state_by_class[name]} if name in initial_state_by_class else set()
            for name in declared
        }
        for scenario_result in scenario_results:
            for step_result in getattr(scenario_result, "step_results", []):
                for record in step_result.transitions_fired:
                    for cls_name, decl_set in declared.items():
                        if record.target in decl_set and record.source in decl_set:
                            entered_by_class[cls_name].add(record.target)

        out: dict[str, dict] = {}
        for cls_name, decl_set in declared.items():
            entered = entered_by_class[cls_name]
            missing = decl_set - entered
            total = len(decl_set)
            out[cls_name] = {
                "total": total,
                "entered": sorted(entered),
                "missing": sorted(missing),
                "coverage": (len(entered) / total) if total else 1.0,
            }
        return out

    def event_path_coverage(self, scenario_results: list) -> dict:
        """
        Compute coverage of declared subscriptions: each (machine, event) pair
        from `subscriptions()` should be triggered by at least one scenario.
        """
        declared: set[tuple[str, str]] = set()
        for cls in self._machine_classes:
            for event_name in cls.subscriptions().keys():
                declared.add((cls.__name__, event_name))

        triggered: set[tuple[str, str]] = set()
        for scenario_result in scenario_results:
            for event in getattr(scenario_result, "events_emitted", []):
                # For each registered subscription, mark triggered if the event matches
                for cls in self._machine_classes:
                    if event.name in cls.subscriptions():
                        triggered.add((cls.__name__, event.name))

        missing = declared - triggered
        return {
            "total": len(declared),
            "triggered": sorted(triggered),
            "missing": sorted(missing),
            "coverage": (len(triggered) / len(declared)) if declared else 1.0,
        }

    def converge(
        self,
        scenarios: list | None = None,
        invariants: list | None = None,
    ):
        """
        Run all three convergence layers and return a ConvergenceReport.

        Args:
            scenarios: List of Scenario objects to execute (Layer 2).
            invariants: List of invariant functions to check (Layer 3).

        Returns:
            ConvergenceReport with structural + scenario + invariant results
            and coverage data.
        """
        from sdd.convergence import ConvergenceReport
        from sdd.scenario import ScenarioRunner

        report = ConvergenceReport()

        # Layer 1: structural
        report.structural = self.check()

        # Layer 2 + 3: scenarios + invariants
        if scenarios:
            scenario_runner = ScenarioRunner(self)
            report.scenario_results = scenario_runner.run_all(
                scenarios, invariants=invariants
            )
            # Coverage
            report.transition_coverage = self.transition_coverage(report.scenario_results)
            report.state_coverage = self.state_coverage(report.scenario_results)
            report.event_path_coverage = self.event_path_coverage(report.scenario_results)
            # Invariant aggregation
            if invariants:
                report.invariant_results = [
                    r.invariant_results
                    for r in report.scenario_results
                    if r.invariant_results is not None
                ]

        return report

    def check(self) -> StructuralReport:
        """
        Run all structural checks on registered machines.

        Returns:
            StructuralReport with results of all checks.
        """
        report = StructuralReport()

        # Analyze each registered machine
        for machine_class in self._machine_classes:
            machine_name = machine_class.__name__
            report.machines_analyzed.append(machine_name)
            report.total_states += len(machine_class._states)
            report.total_transitions += len(machine_class._transitions)

            # Check for unreachable states
            dead = self.dead_states(machine_class)
            if dead:
                report.unreachable_states[machine_name] = sorted(dead)

            # Check for termination issues
            terminal = self.termination_issues(machine_class)
            if terminal:
                report.terminal_states[machine_name] = sorted(terminal)

            # Check for guard-completeness gaps
            gaps = self.guard_completeness_issues(machine_class)
            if gaps:
                report.guard_completeness_issues[machine_name] = sorted(gaps)

        # Cross-machine checks
        report.dead_letters = self.dead_letters()
        report.phantom_subscriptions = self.phantom_subscriptions()

        return report
