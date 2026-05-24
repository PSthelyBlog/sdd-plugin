"""
End-to-end integration test: ProductionRunner + SQLite persistence + HTTP API.

Demonstrates the SDD convergence-to-production handoff. The same OrderLifecycle
machine that passes simulation is driven through an HTTP layer in production,
with state persisted to SQLite. The process-boundary test (tear down, restart,
continue) confirms that snapshot()/restore() actually survives a real restart
and that the final state matches what simulation produces.

No machine code changes between simulation and production. This is the central
SDD claim — verified here for the first time.
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
from pathlib import Path

import pytest

from sdd.adapters import ProductionRunner
from adapters.inbound.http_api import HttpInboundAdapter
from adapters.outbound.sqlite_persistence import SqlitePersistenceAdapter
from machines.order_lifecycle import OrderLifecycle


def _post(url: str, body: dict | None = None) -> tuple[int, dict]:
    """POST helper. Returns (status_code, json_body)."""
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "orders.db")


def _build_runner(db_path: str) -> tuple[ProductionRunner, HttpInboundAdapter, SqlitePersistenceAdapter]:
    """Build a ProductionRunner with both adapters attached."""
    runner = ProductionRunner()
    runner.register(OrderLifecycle)
    runner.register_resolver(OrderLifecycle, lambda e: e.payload.get("order_id"))

    persistence = SqlitePersistenceAdapter(db_path)
    http = HttpInboundAdapter(host="127.0.0.1", port=0)
    runner.attach(persistence)
    runner.attach(http)
    return runner, http, persistence


class TestHttpAdapterBasics:
    def test_unknown_machine_returns_404(self, db_path):
        runner, http, _ = _build_runner(db_path)
        with runner:
            status, body = _post(f"{http.url}/machines/NoSuch/x/go")
            assert status == 404
            assert body["error"] == "unknown_machine"

    def test_unknown_transition_returns_400_with_step_error(self, db_path):
        runner, http, _ = _build_runner(db_path)
        with runner:
            status, body = _post(
                f"{http.url}/machines/OrderLifecycle/ord_x/no_such_transition",
                {"order_id": "ord_x", "customer_id": "c", "items": [{"sku": "A", "quantity": 1}]},
            )
            assert status == 400
            assert len(body["errors"]) == 1
            assert body["errors"][0]["error_type"] == "unknown_transition"


class TestE2EOrderFlow:
    """Drive an order through validate → fulfill → complete via HTTP."""

    def test_happy_path_through_http(self, db_path):
        runner, http, _ = _build_runner(db_path)
        with runner:
            # validate
            status, body = _post(
                f"{http.url}/machines/OrderLifecycle/ord_1/validate",
                {"order_id": "ord_1", "customer_id": "cust_1",
                 "items": [{"sku": "WIDGET", "quantity": 2}]},
            )
            assert status == 200, body
            assert any(t["target"] == "validated" for t in body["transitions_fired"])

            # fulfill
            status, body = _post(
                f"{http.url}/machines/OrderLifecycle/ord_1/fulfill",
                {"warehouse_id": "WH_EAST"},
            )
            assert status == 200, body

            # complete
            status, body = _post(f"{http.url}/machines/OrderLifecycle/ord_1/complete")
            assert status == 200, body

            instance = runner.get(OrderLifecycle, "ord_1")
            assert instance.current_state == "completed"


class TestProcessBoundarySurvival:
    """The headline test: tear down runner mid-scenario, restart, complete."""

    def test_restart_preserves_machine_state(self, db_path):
        # ---- Process 1: start, validate, fulfill, then tear down ----
        runner_a, http_a, _ = _build_runner(db_path)
        runner_a.startup()
        try:
            status, _ = _post(
                f"{http_a.url}/machines/OrderLifecycle/ord_1/validate",
                {"order_id": "ord_1", "customer_id": "cust_1",
                 "items": [{"sku": "WIDGET", "quantity": 2}]},
            )
            assert status == 200
            status, _ = _post(
                f"{http_a.url}/machines/OrderLifecycle/ord_1/fulfill",
                {"warehouse_id": "WH_EAST"},
            )
            assert status == 200
            # Confirm in-memory state right before shutdown
            assert runner_a.get(OrderLifecycle, "ord_1").current_state == "fulfilled"
        finally:
            runner_a.shutdown()

        # ---- Process 2: fresh runner, same DB. Don't pre-load anything. ----
        runner_b, http_b, _ = _build_runner(db_path)
        runner_b.startup()
        try:
            # No in-memory instance for ord_1 — the loader must rehydrate it
            assert runner_b._instances == {}, "fresh runner should have no instances"

            # Now fire `complete`. The HTTP adapter will hit the runner; the
            # loader will rehydrate from SQLite via OrderLifecycle.restore().
            status, body = _post(
                f"{http_b.url}/machines/OrderLifecycle/ord_1/complete",
            )
            # NB: HTTP adapter implicitly creates an instance if get() returns None.
            # But ProductionRunner.get() consults the loader FIRST — so we should
            # get the rehydrated instance, not a fresh one starting at `placed`.
            assert status == 200, body
            assert any(t["target"] == "completed" for t in body["transitions_fired"])

            instance = runner_b.get(OrderLifecycle, "ord_1")
            assert instance.current_state == "completed"
            # And the rehydrated context survived
            assert instance.context["customer_id"] == "cust_1"
            assert instance.context["warehouse_id"] == "WH_EAST"
        finally:
            runner_b.shutdown()


class TestSimulationVsProduction:
    """The SDD invariant: simulation outcomes == production outcomes."""

    def test_final_state_matches_simulation(self, db_path):
        # In simulation (no adapters), the happy path ends in "completed"
        from sdd.runner import SimulationRunner
        sim = SimulationRunner()
        sim.register(OrderLifecycle)
        sim.create(OrderLifecycle, "ord_sim",
                   context={"order_id": "ord_sim", "customer_id": "c",
                            "items": [{"sku": "A", "quantity": 1}]})
        sim.fire("ord_sim", OrderLifecycle, "validate")
        sim.fire("ord_sim", OrderLifecycle, "fulfill", warehouse_id="WH_EAST")
        sim.fire("ord_sim", OrderLifecycle, "complete")
        sim_final = sim.get(OrderLifecycle, "ord_sim").current_state

        # In production (with adapters), same sequence via HTTP
        runner, http, _ = _build_runner(db_path)
        with runner:
            _post(f"{http.url}/machines/OrderLifecycle/ord_p/validate",
                  {"order_id": "ord_p", "customer_id": "c",
                   "items": [{"sku": "A", "quantity": 1}]})
            _post(f"{http.url}/machines/OrderLifecycle/ord_p/fulfill",
                  {"warehouse_id": "WH_EAST"})
            _post(f"{http.url}/machines/OrderLifecycle/ord_p/complete")
            prod_final = runner.get(OrderLifecycle, "ord_p").current_state

        # The whole point of SDD: identical outcomes
        assert sim_final == prod_final == "completed"
