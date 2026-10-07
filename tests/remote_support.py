"""A real local HTTP server for remote include tests (config-remote-schema).

:class:`RemoteServer` runs a ``ThreadingHTTPServer`` on ``127.0.0.1`` with an ephemeral port in a
daemon thread. Tests change its route table while it runs, and it records every request it
receives. It serves in-process unit tests and the real ``troupe`` console script alike, since the
subprocess reaches the same port. No test ever reaches the internet.

The ``remote_server`` fixture in ``tests/conftest.py`` starts one per test, sets ``NO_PROXY`` (the
CLI subprocess inherits it) and stops the server afterwards.
"""

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Self

NO_PROXY_HOSTS = "127.0.0.1,localhost"
"""Hosts that must bypass any proxy configured in the developer's shell."""


@dataclass(frozen=True)
class Route:
    """What the server answers for one path: a status, body bytes and extra headers.

    ``delay`` seconds pass before the answer starts (the timeout tests). ``Content-Length`` is
    always sent for ``body``.
    """

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


@dataclass(frozen=True)
class RecordedRequest:
    """One request the server received: method, path (with query) and headers."""

    method: str
    path: str
    headers: dict[str, str]


class RemoteServer:
    """A threaded HTTP server on 127.0.0.1 with a mutable route table and a request log.

    Unknown paths answer ``404 Not Found``. Use it as a context manager, or call :meth:`start`
    and :meth:`stop`.
    """

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def port(self) -> int:
        """The ephemeral port the server listens on."""
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """``http://127.0.0.1:<port>``, with no trailing slash."""
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        """The full URL of ``path`` (which starts with ``/``) on this server."""
        return f"{self.base_url}{path}"

    def route(
        self,
        path: str,
        body: str | bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
    ) -> str:
        """Serve ``body`` (UTF-8 if text) with ``status`` at ``path``; returns the full URL."""
        data = body.encode("utf-8") if isinstance(body, str) else body
        with self._lock:
            self.routes[path] = Route(status, data, dict(headers or {}), delay)
        return self.url(path)

    def redirect(self, path: str, target: str, *, status: int = 301) -> str:
        """Redirect ``path`` to ``target`` (a path here or a full URL); returns the URL."""
        location = self.url(target) if target.startswith("/") else target
        return self.route(path, status=status, headers={"Location": location})

    def paths(self) -> list[str]:
        """The paths of all recorded requests, in arrival order."""
        with self._lock:
            return [request.path for request in self.requests]

    def start(self) -> Self:
        """Start serving in a daemon thread."""
        self._thread.start()
        return self

    def stop(self) -> None:
        """Release delayed answers, stop serving and close the socket."""
        self._stopping.set()
        if self._thread.is_alive():
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _answer(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self.requests.append(
                RecordedRequest(handler.command, handler.path, dict(handler.headers.items()))
            )
            route = self.routes.get(handler.path.split("?", 1)[0].split("#", 1)[0])
        if route is None:
            route = Route(status=404, body=b"not found")
        if route.delay and self._stopping.wait(route.delay):
            return
        try:
            handler.send_response(route.status)
            for name, value in route.headers.items():
                handler.send_header(name, value)
            handler.send_header("Content-Length", str(len(route.body)))
            handler.end_headers()
            if handler.command != "HEAD":
                handler.wfile.write(route.body)
        except (BrokenPipeError, ConnectionResetError):
            # The client gave up (a timeout test); nothing is left to answer.
            pass

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server._answer(self)

            def do_HEAD(self) -> None:
                server._answer(self)

            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler
