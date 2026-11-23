"""The way out for pipeline events (plan D8).

Every project event leaves the runtime through an :class:`EventBus`: the runtime publishes, and
whoever wants the events subscribes (today: the WebSocket delivery). In one process the bus is a
plain in-memory fan-out, :class:`InProcessEventBus`. When the API and the workers become separate
processes (phase 6) a Redis pub/sub implementation of the same interface carries the events from
the worker to the process that holds the WebSocket connections, and nothing else has to change.

Delivery is in subscription order and awaited, one subscriber after the other, so per project the
events reach each subscriber in the order they were published. A subscriber that raises is logged
and skipped: it never blocks the others or the publisher.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# subscriber(project_id, event, data)
EventHandler = Callable[[str, str, dict], Awaitable[None]]


@runtime_checkable
class EventBus(Protocol):
    async def publish(self, project_id: str, event: str, data: dict) -> None:
        """Hand ``event`` of ``project_id`` to every subscriber; never raises for a subscriber."""

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Receive every published event from now on; returns a function that unsubscribes."""


class InProcessEventBus:
    """Single-process bus: ``publish`` awaits each subscriber in turn."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._handlers:
                self._handlers.remove(handler)

        return unsubscribe

    async def publish(self, project_id: str, event: str, data: dict) -> None:
        for handler in list(self._handlers):  # a copy: handlers may unsubscribe while delivering
            try:
                await handler(project_id, event, data)
            except Exception:
                logger.exception("Event subscriber failed on %s of project %s", event, project_id)
