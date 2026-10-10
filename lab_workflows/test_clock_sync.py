"""参考时钟同步两阶段时序的无硬件回归检查。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from . import clock_sync
from .clock_sync import synchronize_clocks


class _FakeGenerator:
    """记录读写顺序的信号源假件；fail_set 控制设置是否失败。"""

    def __init__(self, events: list[tuple[str, str]], name: str,
                 *, fail_set: bool = False, actual: str = "EXTernal",
                 sticky: bool = False) -> None:
        self._events = events
        self._name = name
        self._fail_set = fail_set
        self._actual = actual
        self._sticky = sticky  # 模拟设置后回读仍返回旧值的设备

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def get_ref_clock_source(self) -> str:
        self._events.append(("get", self._name))
        return self._actual

    def set_ref_clock_source(self, source: str) -> None:
        if self._fail_set:
            raise RuntimeError("set timeout")
        self._events.append(("set", self._name))
        if not self._sticky:
            self._actual = source


def _specs() -> list[dict]:
    def spec(name: str) -> dict:
        return {
            "type": "DG4000",
            "resource": f"USB0::{name}",
            "short": name,
            "label": name,
            "channels": {1},
            "config": {"name": name},
        }
    return [spec("A"), spec("B"), spec("C")]


def _run_sync(events: list, *, fail_names: frozenset[str] = frozenset(),
              mismatch_name: str | None = None) -> list:
    generators = {
        name: _FakeGenerator(
            events, name,
            fail_set=name in fail_names,
            actual="INTernal" if name == mismatch_name else "EXTernal",
            sticky=name == mismatch_name,
        )
        for name in ("A", "B", "C")
    }
    with patch.object(clock_sync, "_discover_clock_devices", return_value=_specs()), \
         patch.object(clock_sync, "create_signal_generator",
                      side_effect=lambda cfg, channel, apply_load_policy=True:
                      generators[cfg["name"]]), \
         patch.object(clock_sync, "load_clock_profile",
                      return_value={"__default__": "EXT"}):
        return synchronize_clocks()


class TwoPhaseClockSyncTests(unittest.TestCase):
    def test_all_sets_before_any_verify_get(self):
        events: list = []
        results = _run_sync(events)
        self.assertTrue(all(r.ok for r in results))
        # 每台先读原值再写入，全部写完后才统一校验回读。
        ops = [op for op, _ in events]
        self.assertEqual(ops, ["get", "set"] * 3 + ["get"] * 3)
        last_set = len(ops) - 1 - ops[::-1].index("set")
        self.assertEqual(ops[last_set + 1:], ["get", "get", "get"])

    def test_set_failure_skips_readback_only_for_that_device(self):
        events: list = []
        results = _run_sync(events, fail_names={"B"})
        by_label = {r.label: r for r in results}
        self.assertFalse(by_label["B"].ok)
        self.assertIn("set timeout", by_label["B"].error)
        self.assertTrue(by_label["A"].ok)
        self.assertTrue(by_label["C"].ok)
        self.assertEqual([name for op, name in events if op == "set"], ["A", "C"])

    def test_readback_mismatch_reported(self):
        events: list = []
        results = _run_sync(events, mismatch_name="C")
        by_label = {r.label: r for r in results}
        self.assertTrue(by_label["A"].ok)
        self.assertFalse(by_label["C"].ok)
        self.assertIn("期望 EXT，实际 INT", by_label["C"].error)


if __name__ == "__main__":
    unittest.main()
