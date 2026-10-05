"""Request-scoped application-dispatch capture. Never changes tool permissions."""

import asyncio
import copy
import inspect
from contextvars import ContextVar
from threading import RLock

from .contracts import bounded_document, identifier

_ACTIVE = ContextVar("promptbrake_tool_capture", default=None)


class Capture:
    def __init__(self, request_id, dispatcher=None):
        if not identifier(request_id):
            raise ValueError("Use a bounded ASCII request identifier.")
        self.request_id = request_id
        self.dispatcher = dispatcher
        self.calls = []
        self.covered = set()
        self.pending = 0
        self.damaged = False
        self.closed = False
        self.entered = False
        self.lock = RLock()

    def __enter__(self):
        if self.entered:
            raise ValueError("Capture sessions cannot be reused.")
        self.entered = True
        parent = _ACTIVE.get()
        if parent is not None:
            with parent.lock:
                parent.damaged = True
        if self.dispatcher is not None:
            self.covered.update(self.dispatcher.names)
        self.token = _ACTIVE.set(self)
        return self

    def __exit__(self, kind, value, tb):
        with self.lock:
            self.damaged |= kind is not None or self.pending > 0
            self.closed = True
        _ACTIVE.reset(self.token)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, kind, value, tb):
        return self.__exit__(kind, value, tb)

    def start(self, dispatcher, tool, arguments):
        with self.lock:
            if self.closed:
                self.damaged = True
                return False
            self.covered.update(dispatcher.names)
            self.pending += 1
            try:
                bounded_document(arguments, 8192)
                if type(arguments) is not dict or len(self.calls) >= 100:
                    raise ValueError("Capture limit.")
                self.calls.append(
                    {"sequence": len(self.calls) + 1, "tool": tool, "arguments": copy.deepcopy(arguments)}
                )
            except (ValueError, TypeError, RecursionError, UnicodeError):
                self.damaged = True
            return True

    def finish(self):
        with self.lock:
            self.pending -= 1

    def trace(self):
        with self.lock:
            result = {
                "version": 1,
                "request_id": self.request_id,
                "source": "application_dispatcher",
                "complete": self.closed and not self.damaged and self.pending == 0,
                "covered_tools": sorted(self.covered),
                "calls": copy.deepcopy(self.calls),
            }
            try:
                bounded_document(result, 1048576)
            except (ValueError, UnicodeError):
                result["complete"] = False
                result["calls"] = []
            return result


def capture(request_id, dispatcher=None):
    """Pass the actual dispatcher so a zero-call run can establish coverage."""
    return Capture(request_id, dispatcher)


class ToolDispatcher:
    def __init__(self, tools):
        if (
            type(tools) is not dict
            or not 1 <= len(tools) <= 100
            or any(not identifier(k) or not callable(v) for k, v in tools.items())
        ):
            raise ValueError("Register 1 to 100 named callable tools.")
        self._tools = dict(tools)
        self.names = tuple(sorted(tools))

    def call(self, tool, arguments):
        function = self._tools[tool]
        session = _ACTIVE.get()
        tracked = session.start(self, tool, arguments) if session else False
        try:
            result = function(**arguments)
            if inspect.isawaitable(result):
                if session:
                    session.damaged = True
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("Use acall for asynchronous tools.")
            return result
        finally:
            if tracked:
                session.finish()

    async def acall(self, tool, arguments):
        function = self._tools[tool]
        session = _ACTIVE.get()
        tracked = session.start(self, tool, arguments) if session else False
        try:
            result = function(**arguments)
            return await result if inspect.isawaitable(result) else result
        except asyncio.CancelledError:
            if session:
                with session.lock:
                    session.damaged = True
            raise
        finally:
            if tracked:
                session.finish()
