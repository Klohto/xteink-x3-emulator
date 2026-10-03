import unittest

from x3emu import VirtualClock


class VirtualClockTests(unittest.TestCase):
    def test_time_order_and_fifo_ties(self) -> None:
        clock = VirtualClock()
        seen: list[tuple[str, int]] = []
        for name, time_ns in (("last", 20), ("first", 10), ("second", 10)):
            clock.schedule_at(time_ns, lambda name=name: seen.append((name, clock.now_ns)))

        self.assertEqual(clock.advance_to(20), 3)
        self.assertEqual(seen, [("first", 10), ("second", 10), ("last", 20)])
        self.assertEqual(clock.now_ns, 20)

    def test_relative_events_are_deferred_until_due_inclusively(self) -> None:
        clock = VirtualClock(now_ns=50)
        seen: list[int] = []
        clock.schedule_after(10, lambda: seen.append(clock.now_ns))
        clock.schedule_after(20, lambda: seen.append(clock.now_ns))

        self.assertEqual(clock.advance_by(9), 0)
        self.assertEqual(clock.now_ns, 59)
        self.assertEqual(clock.advance_to(60), 1)
        self.assertEqual(seen, [60])
        self.assertEqual(clock.advance_by(5), 0)
        self.assertEqual(clock.now_ns, 65)
        self.assertEqual(clock.advance_to(70), 1)
        self.assertEqual(seen, [60, 70])

    def test_cancelled_events_do_not_run_or_move_time_past_target(self) -> None:
        clock = VirtualClock()
        event = clock.schedule_at(100, lambda: self.fail("cancelled callback ran"))
        self.assertEqual(event.time_ns, 100)
        self.assertTrue(event.cancel())
        self.assertTrue(event.cancelled)
        self.assertFalse(event.cancel())

        self.assertEqual(clock.advance_to(10), 0)
        self.assertEqual(clock.now_ns, 10)
        self.assertEqual(clock.advance_to(100), 0)
        self.assertEqual(clock.now_ns, 100)

    def test_callback_can_cancel_another_event_at_the_same_time(self) -> None:
        clock = VirtualClock()
        clock.schedule_at(10, lambda: event.cancel())
        event = clock.schedule_at(10, lambda: self.fail("cancelled callback ran"))

        self.assertEqual(clock.advance_to(10), 1)
        self.assertTrue(event.cancelled)

    def test_completed_event_cannot_be_cancelled(self) -> None:
        clock = VirtualClock()
        event = clock.schedule_after(0, lambda: None)
        self.assertEqual(clock.advance_by(0), 1)
        self.assertFalse(event.cancel())
        self.assertFalse(event.cancelled)

    def test_callback_scheduling_same_time_preserves_fifo_and_target(self) -> None:
        clock = VirtualClock()
        seen: list[str] = []

        def first() -> None:
            seen.append("first")
            clock.schedule_after(0, lambda: seen.append("third"))
            clock.schedule_after(1, lambda: seen.append("later"))

        clock.schedule_at(10, first)
        clock.schedule_at(10, lambda: seen.append("second"))

        self.assertEqual(clock.advance_to(10), 3)
        self.assertEqual(seen, ["first", "second", "third"])
        self.assertEqual(clock.now_ns, 10)
        self.assertEqual(clock.advance_by(1), 1)
        self.assertEqual(seen, ["first", "second", "third", "later"])

    def test_invalid_times_leave_time_unchanged(self) -> None:
        clock = VirtualClock(now_ns=10)
        for operation in (
            lambda: VirtualClock(-1),
            lambda: clock.schedule_at(9, lambda: None),
            lambda: clock.schedule_at(-1, lambda: None),
            lambda: clock.schedule_after(-1, lambda: None),
            lambda: clock.advance_to(9),
            lambda: clock.advance_by(-1),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
        self.assertEqual(clock.now_ns, 10)

    def test_times_must_be_integer_nanoseconds(self) -> None:
        clock = VirtualClock()
        for value in (1.5, True, "1", None):
            for operation in (
                lambda: VirtualClock(value),
                lambda: clock.schedule_at(value, lambda: None),
                lambda: clock.schedule_after(value, lambda: None),
                lambda: clock.advance_to(value),
                lambda: clock.advance_by(value),
            ):
                with self.subTest(value=value, operation=operation):
                    with self.assertRaises(TypeError):
                        operation()
        self.assertEqual(clock.now_ns, 0)

    def test_callback_must_be_callable(self) -> None:
        clock = VirtualClock()
        with self.assertRaises(TypeError):
            clock.schedule_at(0, None)
        self.assertEqual(clock.advance_to(0), 0)

    def test_callback_exception_stops_at_event_and_allows_resume(self) -> None:
        clock = VirtualClock()
        seen: list[int] = []

        def fail() -> None:
            raise LookupError("callback failed")

        failed_event = clock.schedule_at(10, fail)
        clock.schedule_at(15, lambda: seen.append(clock.now_ns))
        with self.assertRaisesRegex(LookupError, "callback failed"):
            clock.advance_to(20)

        self.assertEqual(clock.now_ns, 10)
        self.assertFalse(failed_event.cancel())
        self.assertEqual(clock.advance_to(20), 1)
        self.assertEqual(seen, [15])
        self.assertEqual(clock.now_ns, 20)

    def test_recursive_advance_cannot_break_monotonic_time(self) -> None:
        clock = VirtualClock()

        def callback() -> None:
            with self.assertRaises(RuntimeError):
                clock.advance_to(100)

        clock.schedule_at(5, callback)
        self.assertEqual(clock.advance_to(10), 1)
        self.assertEqual(clock.now_ns, 10)

    def test_large_integer_times_keep_nanosecond_precision(self) -> None:
        start = 10**20
        clock = VirtualClock(now_ns=start)
        seen: list[int] = []
        clock.schedule_after(1, lambda: seen.append(clock.now_ns))

        self.assertEqual(clock.advance_to(start), 0)
        self.assertEqual(clock.advance_by(1), 1)
        self.assertEqual(seen, [start + 1])


if __name__ == "__main__":
    unittest.main()
