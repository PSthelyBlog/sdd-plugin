"""
Virtual clock and @timeout decorator.

The simulation runner maintains a virtual clock (seconds since reset). Machines
declare time-based transitions via the @timeout decorator:

    class PaymentFlow(StateMachine):
        pending = State(initial=True)
        expired = State(final=True)
        expire = pending.to(expired)

        @timeout(expire, seconds=24 * 3600)
        def deadline(self):
            self.context["expiry_reason"] = "authorization_timeout"

The decorated method runs after the timeout fires (analogous to an on_transition_*
hook). When the runner's clock advances past the deadline for an instance, the
runner fires the named transition.

Timeouts are per-(instance, state-entry): when an instance enters a state that
has timeouts declared, deadlines are scheduled. When it leaves that state,
pending deadlines for that state are cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TimeoutDeclaration:
    """A declarative record attached to a method by @timeout."""

    transition_name: str
    delay_seconds: float
    source_state: str | None = None  # If None, inferred from transition's first source


def timeout(
    transition: Any,
    *,
    seconds: float = 0,
    minutes: float = 0,
    hours: float = 0,
    days: float = 0,
    from_state: str | None = None,
):
    """
    Decorator declaring a time-based transition.

    Args:
        transition: The Transition descriptor (e.g., `expire` defined above)
            OR a string transition name.
        seconds/minutes/hours/days: Cumulative delay. At least one must be > 0.
        from_state: Optional explicit source state name. If omitted, inferred
            from the transition's first declared source.

    The decorated method is invoked after the timeout-driven transition fires,
    allowing it to set context (e.g., a reason field).
    """
    total = seconds + 60 * minutes + 3600 * hours + 86400 * days
    if total <= 0:
        raise ValueError("@timeout requires a positive delay")

    # Determine the transition name. `transition` may be the Transition
    # descriptor (in which case its `.name` is set by __set_name__ once the
    # class body finishes) or a plain string. We accept both.
    if isinstance(transition, str):
        transition_name = transition
        inferred_source = from_state
    else:
        # Transition descriptor — but at class-body evaluation time its
        # .name may not be set yet (__set_name__ runs after the body).
        # The metaclass resolves it; here we lazily capture the object.
        transition_name = None  # filled in by the metaclass later
        inferred_source = from_state

    def decorator(method: Callable) -> Callable:
        decl = TimeoutDeclaration(
            transition_name=transition_name or "",
            delay_seconds=total,
            source_state=inferred_source,
        )
        # Attach for metaclass discovery and for runtime use
        method._sdd_timeout = decl
        # Also attach the unresolved transition object so the metaclass
        # can fill in the name after __set_name__
        method._sdd_timeout_transition_obj = transition if not isinstance(transition, str) else None
        return method

    return decorator
