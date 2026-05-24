"""
Adapter protocol and ProductionRunner.

Adapters connect a state machine core to real infrastructure. They are written
**after convergence**. The framework defines only the contract here — concrete
adapters live in `adapters/inbound/` and `adapters/outbound/` in each project.

Two kinds of adapters:

- **Outbound**: subscribe to events on the runner's bus, perform external I/O.
  These attach by calling `runner.event_bus.subscribe(...)` in `attach()`.

- **Inbound**: translate external signals into `runner.fire(...)` calls.
  These typically expose a callable interface (HTTP endpoint, CLI command,
  queue consumer) that the host application invokes.

The persistence adapter is bidirectional — it stores machine snapshots on
events (outbound) and rehydrates instances on demand via the runner's
`set_instance_loader()` hook (inbound).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from sdd.runner import SimulationRunner

if TYPE_CHECKING:
    from sdd.events import Event
    from sdd.protocol import StateMachine


@runtime_checkable
class Adapter(Protocol):
    """
    Protocol an adapter must satisfy to attach to a ProductionRunner.

    Implementers can be any class with these methods. `attach()` is required;
    `startup()` and `shutdown()` are optional lifecycle hooks.
    """

    def attach(self, runner: "ProductionRunner") -> None:
        """Wire up subscriptions, register loaders, etc."""
        ...


InstanceLoader = Callable[[type, str], "StateMachine | None"]
"""Function called when an event targets an instance not currently in memory.

Signature: `(machine_class, instance_id) -> StateMachine | None`.
Should return a rehydrated instance (e.g., from persistent storage) or None
if no such instance exists.
"""


class ProductionRunner(SimulationRunner):
    """
    SimulationRunner extended with adapter lifecycle and instance rehydration.

    The core fire/event/structural/coverage logic is inherited unchanged — the
    machines behave identically under simulation and in production. Only the
    adapter wiring and instance loading differ.
    """

    def __init__(self) -> None:
        super().__init__()
        self._adapters: list[Adapter] = []
        self._instance_loader: InstanceLoader | None = None
        self._started: bool = False

    # ---------- adapter lifecycle ----------

    def attach(self, adapter: Adapter) -> None:
        """Register an adapter. Calls adapter.attach(self) so it can wire up."""
        self._adapters.append(adapter)
        adapter.attach(self)

    def startup(self) -> None:
        """Call startup() on every adapter that implements it."""
        for adapter in self._adapters:
            startup = getattr(adapter, "startup", None)
            if callable(startup):
                startup()
        self._started = True

    def shutdown(self) -> None:
        """Call shutdown() on every adapter that implements it (reverse order)."""
        for adapter in reversed(self._adapters):
            shutdown = getattr(adapter, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self._started = False

    @property
    def adapters(self) -> list[Adapter]:
        """Return a copy of the attached adapter list."""
        return list(self._adapters)

    @property
    def started(self) -> bool:
        return self._started

    # ---------- instance loading (persistence hook) ----------

    def set_instance_loader(self, loader: InstanceLoader) -> None:
        """
        Register a callback for rehydrating instances not in memory.

        When `get(machine_class, instance_id)` returns None during event
        routing, the loader is called. If it returns an instance, that
        instance is added to the registry and used. If it returns None,
        the normal implicit-creation logic applies.
        """
        self._instance_loader = loader

    def get(self, machine_class, instance_id):
        """
        Override SimulationRunner.get to consult the instance loader when
        the instance isn't already in memory.
        """
        instance = self._instances.get((machine_class, instance_id))
        if instance is not None:
            return instance

        if self._instance_loader is None:
            return None

        loaded = self._instance_loader(machine_class, instance_id)
        if loaded is None:
            return None

        # Connect the loaded instance to our bus and register it
        loaded._event_bus = self._event_bus
        loaded._instance_id = instance_id
        self._instances[(machine_class, instance_id)] = loaded
        return loaded

    # ---------- context-manager convenience ----------

    def __enter__(self) -> "ProductionRunner":
        self.startup()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()
