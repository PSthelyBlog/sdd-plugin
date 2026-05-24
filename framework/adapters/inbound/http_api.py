"""
HTTP inbound adapter (stdlib http.server, no external dependencies).

Routes POST requests of the form:

    POST /machines/{MachineClassName}/{instance_id}/{transition_name}

The request body, if present and JSON, is passed as keyword arguments to
the transition. Returns the StepResult serialized to JSON.

This adapter contains no domain logic. It does three things:
  1. Deserialize JSON body
  2. Resolve which machine class + instance + transition to fire
  3. Call runner.fire(...) and serialize the result

Errors:
  - Unknown machine class → 404
  - StepResult.errors non-empty (e.g., invalid_source_state, schema_validation_failed)
    → 400 with the error list in the body
  - Routing failures present → 400 with details
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

from dataclasses import asdict

if TYPE_CHECKING:
    from sdd.adapters import ProductionRunner


class HttpInboundAdapter:
    """Spawns an http.server on a background thread when started."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        # port=0 means "pick a free port" — handy for tests
        self.host = host
        self.port = port
        self._runner: "ProductionRunner | None" = None
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------- adapter lifecycle ----------

    def attach(self, runner: "ProductionRunner") -> None:
        self._runner = runner

    def startup(self) -> None:
        assert self._runner is not None, "attach() must be called before startup()"
        runner = self._runner

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Suppress default stdout logging during tests
                pass

            def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler convention)
                parts = [p for p in self.path.split("/") if p]
                # Expected: ["machines", MachineName, instance_id, transition]
                if len(parts) != 4 or parts[0] != "machines":
                    self._json(404, {"error": "not_found", "path": self.path})
                    return

                _, machine_name, instance_id, transition_name = parts

                machine_class = runner.get_machine_class(machine_name)
                if machine_class is None:
                    self._json(404, {"error": "unknown_machine", "machine": machine_name})
                    return

                # Parse body as JSON kwargs (optional)
                length = int(self.headers.get("Content-Length") or 0)
                kwargs: dict = {}
                if length:
                    raw = self.rfile.read(length).decode("utf-8")
                    try:
                        kwargs = json.loads(raw) if raw else {}
                    except json.JSONDecodeError as exc:
                        self._json(400, {"error": "invalid_json", "message": str(exc)})
                        return

                # If no instance exists yet, create it from kwargs (mimicking the
                # implicit-creation behavior used inside event routing)
                if runner.get(machine_class, instance_id) is None:
                    runner.create(machine_class, instance_id, context=dict(kwargs))

                result = runner.fire(instance_id, machine_class, transition_name, **kwargs)

                response = {
                    "trigger": result.trigger,
                    "transitions_fired": [
                        {"transition": r.transition, "source": r.source, "target": r.target}
                        for r in result.transitions_fired
                    ],
                    "events_emitted": [
                        {"name": e.name, "payload": e.payload}
                        for e in result.events_emitted
                    ],
                    "cascade_depth": result.cascade_depth,
                    "errors": [
                        {
                            "machine": e.machine,
                            "instance": e.instance,
                            "transition": e.transition,
                            "error_type": e.error_type,
                            "message": e.message,
                            "machine_state_at_error": e.machine_state_at_error,
                        }
                        for e in result.errors
                    ],
                    "routing_failures": [
                        asdict(rf) for rf in result.routing_failures
                    ],
                }

                status = 400 if (result.errors or result.routing_failures) else 200
                self._json(status, response)

            def _json(self, status: int, body: dict) -> None:
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = HTTPServer((self.host, self.port), _Handler)
        # If port=0 was used, capture the actual port the OS assigned
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    # ---------- introspection ----------

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"
