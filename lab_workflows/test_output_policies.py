"""信号发生器负载和时序收尾策略的无硬件回归检查。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from . import devices
from .common import load_mapping
from .devices import signal_generator_load_policy
from .steps.safety_shutdown import DGChannelShutdown, run_safety_shutdown


class _FakeGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def set_burst_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("burst", channel))

    def set_mod_state(self, state: bool, *, channel: int) -> None:
        self.calls.append(("mod", channel))

    def setup_dc(self, value: float, *, channel: int) -> None:
        self.calls.append(("dc", channel))

    def set_output(self, state: bool, *, channel: int) -> None:
        self.calls.append(("output", channel))


class _FakeDriver:
    def __init__(self, resource: str, **kwargs) -> None:
        self.resource = resource
        self.kwargs = kwargs


class OutputPolicyTests(unittest.TestCase):
    def test_factory_passes_policy_to_every_generator_resource(self) -> None:
        mapping = load_mapping()
        resources = {
            config["resource"]: config
            for config in mapping.values()
            if config.get("instrument") == "signal_generator"
        }
        for resource, config in resources.items():
            model = str(config["model"]).upper()
            with self.subTest(resource=resource):
                with patch.dict(devices.SIGNAL_GENERATOR_DRIVERS, {model: _FakeDriver}):
                    generator = devices.create_signal_generator(resource, channel=1)
                self.assertEqual(
                    generator.kwargs["load_policy"],
                    signal_generator_load_policy(resource),
                )

    def test_load_policy(self) -> None:
        mapping = load_mapping()
        pump_resource = mapping["Pump_modulation"]["resource"]
        z_resource = mapping["Z_magnetic_field"]["resource"]
        self.assertEqual(
            signal_generator_load_policy(pump_resource),
            {1: 50.0, 2: "INF"},
        )
        self.assertEqual(
            signal_generator_load_policy(z_resource),
            {1: "INF", 2: "INF"},
        )

    def test_timing_channels_are_not_touched_by_shutdown(self) -> None:
        timing = _FakeGenerator()
        z_field = _FakeGenerator()
        report = run_safety_shutdown(
            dg_channels=(
                DGChannelShutdown(timing, 2, "Time_sequence_2", "共同触发"),
                DGChannelShutdown(z_field, 1, "Z_magnetic_field", "Z 场"),
            )
        )
        self.assertEqual(timing.calls, [])
        self.assertEqual(
            z_field.calls,
            [("burst", 1), ("mod", 1), ("dc", 1), ("output", 1)],
        )
        self.assertIn("Time_sequence_2", report.preserved_outputs)


if __name__ == "__main__":
    unittest.main()
