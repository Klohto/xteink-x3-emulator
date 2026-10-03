"""A deterministic event clock. All times are integer nanoseconds."""

from collections.abc import Callable
import heapq
from itertools import count


def _require_ns(value: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer number of nanoseconds")
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


class ScheduledEvent:
    """A handle for one callback. Cancellation takes effect before dispatch."""

    __slots__ = ("_time_ns", "_callback", "_cancelled", "_pending")

    def __init__(self, time_ns: int, callback: Callable[[], None]) -> None:
        self._time_ns = time_ns
        self._callback = callback
        self._cancelled = False
        self._pending = True

    @property
    def time_ns(self) -> int:
        return self._time_ns

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> bool:
        """Cancel a pending callback; return whether its state changed."""
        if not self._pending:
            return False
        self._pending = False
        self._cancelled = True
        return True


class VirtualClock:
    """Run scheduled callbacks in time order, using FIFO for equal times.

    This clock assigns no device latency. A model must supply each delay.
    Callbacks may schedule or cancel events. They must not advance the clock.
    """

    def __init__(self, now_ns: int = 0) -> None:
        self._now_ns = _require_ns(now_ns, "now_ns")
        self._events: list[tuple[int, int, ScheduledEvent]] = []
        self._sequence = count()
        self._advancing = False

    @property
    def now_ns(self) -> int:
        return self._now_ns

    def schedule_at(self, time_ns: int, callback: Callable[[], None]) -> ScheduledEvent:
        """Schedule a callback at or after the current time."""
        _require_ns(time_ns, "time_ns")
        if time_ns < self._now_ns:
            raise ValueError("cannot schedule an event in the past")
        if not callable(callback):
            raise TypeError("callback must be callable")
        event = ScheduledEvent(time_ns, callback)
        heapq.heappush(self._events, (time_ns, next(self._sequence), event))
        return event

    def schedule_after(self, delay_ns: int, callback: Callable[[], None]) -> ScheduledEvent:
        """Schedule a callback relative to the current time."""
        _require_ns(delay_ns, "delay_ns")
        return self.schedule_at(self._now_ns + delay_ns, callback)

    def advance_to(self, target_ns: int) -> int:
        """Run callbacks due through target_ns, then set time to target_ns.

        Return the number of dispatched callbacks. A callback exception stops
        the advance at that event's time and propagates to the caller.
        """
        _require_ns(target_ns, "target_ns")
        if target_ns < self._now_ns:
            raise ValueError("cannot move the clock backward")
        if self._advancing:
            raise RuntimeError("callbacks cannot advance the clock")

        executed = 0
        self._advancing = True
        try:
            while self._events and self._events[0][0] <= target_ns:
                time_ns, _, event = heapq.heappop(self._events)
                if event._cancelled:
                    continue
                self._now_ns = time_ns
                event._pending = False
                event._callback()
                executed += 1
            self._now_ns = target_ns
        finally:
            self._advancing = False
        return executed

    def advance_by(self, delta_ns: int) -> int:
        """Advance by a nonnegative duration and return the dispatch count."""
        _require_ns(delta_ns, "delta_ns")
        return self.advance_to(self._now_ns + delta_ns)
