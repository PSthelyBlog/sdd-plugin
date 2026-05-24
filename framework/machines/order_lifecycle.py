"""OrderLifecycle state machine - reference implementation for Phase 2."""

from sdd.protocol import State, StateMachine


class OrderLifecycle(StateMachine):
    """
    Manages the lifecycle of an order from creation to completion or cancellation.

    States:
        created (initial) → validated → fulfilled → completed (final)
                                                  ↘ cancelled (final)

    Context fields:
        order_id: str - Unique identifier for the order
        customer_id: str - Customer placing the order
        items: list[dict] - Order line items with 'sku' and 'quantity'
        warehouse_id: str - Assigned warehouse (set during fulfillment)
        cancellation_reason: str - Reason for cancellation (if cancelled)
    """

    # States
    created = State(initial=True)
    validated = State()
    fulfilled = State()
    completed = State(final=True)
    cancelled = State(final=True)

    # Transitions
    validate = created.to(validated) | created.to(cancelled)
    fulfill = validated.to(fulfilled)
    complete = fulfilled.to(completed)
    cancel = (created | validated | fulfilled).to(cancelled)

    # Guards

    def guard_validate_to_validated(self, **kwargs) -> bool:
        """Order is valid if it has items with positive quantities."""
        items = self._context.get("items", [])
        if not items:
            return False
        return all(
            isinstance(item, dict)
            and item.get("sku")
            and item.get("quantity", 0) > 0
            for item in items
        )
    # guard_validate_to_cancelled omitted intentionally — cancelled is the
    # fallback branch when validation fails. Per the guard-completeness rule,
    # leaving the last branch un-guarded makes the transition total.

    # Side effects - emit events on state entry

    def on_enter_validated(self) -> None:
        """Emit order.validated event."""
        self.emit(
            "order.validated",
            {
                "order_id": self._context.get("order_id"),
                "items": self._context.get("items", []),
                "customer_id": self._context.get("customer_id"),
            },
        )

    def on_enter_fulfilled(self) -> None:
        """Emit order.fulfilled event."""
        self.emit(
            "order.fulfilled",
            {
                "order_id": self._context.get("order_id"),
                "warehouse_id": self._context.get("warehouse_id"),
            },
        )

    def on_enter_completed(self) -> None:
        """Emit order.completed event."""
        self.emit(
            "order.completed",
            {
                "order_id": self._context.get("order_id"),
            },
        )

    def on_enter_cancelled(self) -> None:
        """Emit order.cancelled event."""
        self.emit(
            "order.cancelled",
            {
                "order_id": self._context.get("order_id"),
                "reason": self._context.get("cancellation_reason", "unspecified"),
            },
        )

    @classmethod
    def subscriptions(cls) -> dict[str, str]:
        """
        Map external events to transitions.

        This machine doesn't subscribe to external events directly,
        but downstream machines (e.g., Inventory, Payment) would.
        """
        return {}
