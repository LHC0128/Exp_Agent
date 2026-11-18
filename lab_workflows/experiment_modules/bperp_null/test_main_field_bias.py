"""GS200 主磁场直流偏置的假仪器回归：默认关闭不变、开启时设流与回读、回读失败拒绝、A04 复原、参数校验。

只用假 GS200 与依赖注入，不连接任何硬件。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from ... import common
from ...steps import restore_main_field_state
from . import models, preparation


class FakeGS200:
    def __init__(self, current_a=0.0, output=False, readback_offset_a=0.0, refuse_output=False):
        self.current_a, self.output, self.offset, self.refuse = current_a, output, readback_offset_a, refuse_output
        self.range_a, self.limit_a, self.function = 0.01, 0.1, "CURR"
        self.events = []

    def get_source_function(self):
        return self.function

    def set_source_function(self, value):
        self.events.append(("function", value)); self.function = value

    def get_current(self):
        return self.current_a + self.offset

    def set_current(self, value):
        self.events.append(("current", value)); self.current_a = value

    def get_current_range(self):
        return self.range_a

    def set_current_range(self, value):
        self.events.append(("range", value)); self.range_a = value

    def get_current_limit(self):
        return self.limit_a

    def set_current_limit(self, value):
        self.events.append(("limit", value)); self.limit_a = value

    def get_output(self):
        return self.output

    def set_output(self, state):
        self.events.append(("output", state))
        self.output = bool(state) and not self.refuse


def _ctx(params, gs200):
    return SimpleNamespace(params=params, devices={"gs200": gs200}, root=common.find_project_root(),
                           mapping={"main_magnetic_field": {"resource": "TCPIP0::fake::INSTR"}},
                           state={}, outputs={}, main_field_state=None)


class MainFieldBiasTest(unittest.TestCase):
    def test_default_keeps_output_off(self):
        gs = FakeGS200(current_a=0.008, output=True)
        ctx = _ctx(models.BperpNullParams(), gs)
        preparation.configure_main_field(ctx)
        self.assertFalse(gs.output)
        self.assertNotIn("current", [e[0] for e in gs.events])
        self.assertEqual(ctx.state["main_field"]["output_after"], False)

    def test_enabled_sets_current_then_output_and_restores(self):
        gs = FakeGS200(current_a=0.008, output=False)
        ctx = _ctx(models.BperpNullParams(main_field_enabled=True, main_field_current_ma=0.4), gs)
        preparation.configure_main_field(ctx)
        names = [e[0] for e in gs.events]
        self.assertLess(names.index("current"), max(i for i, e in enumerate(gs.events) if e == ("output", True)))
        self.assertEqual(gs.events[0], ("output", False))
        self.assertAlmostEqual(gs.current_a, 0.0004)
        self.assertTrue(gs.output)
        self.assertAlmostEqual(ctx.state["main_field"]["readback_current_ma"], 0.4)
        self.assertIn("+0.4 mA", ctx.outputs["主磁场"])
        restore_main_field_state(gs, ctx.main_field_state)       # A04 使用的同一复原步骤
        self.assertAlmostEqual(gs.current_a, 0.008)
        self.assertFalse(gs.output)

    def test_readback_mismatch_keeps_output_off(self):
        gs = FakeGS200(readback_offset_a=5e-5)
        ctx = _ctx(models.BperpNullParams(main_field_enabled=True, main_field_current_ma=-0.4), gs)
        with self.assertRaises(preparation.SafetyAbort):
            preparation.configure_main_field(ctx)
        self.assertFalse(gs.output)
        self.assertIsNotNone(ctx.main_field_state)                # 快照已记录，A04 仍能复原

    def test_output_refused_aborts(self):
        gs = FakeGS200(refuse_output=True)
        ctx = _ctx(models.BperpNullParams(main_field_enabled=True, main_field_current_ma=0.4), gs)
        with self.assertRaises(preparation.SafetyAbort):
            preparation.configure_main_field(ctx)

    def test_params_validation(self):
        self.assertTrue(any("MAIN_FIELD_CURRENT_MA" in e for e in
                            models.BperpNullParams(main_field_enabled=True).validate_model()))
        root = common.find_project_root()
        self.assertTrue(any("MAIN_FIELD_CURRENT_MA" in e for e in
                            models.BperpNullParams(main_field_enabled=True, main_field_current_ma=25.0).validate(root)))
        self.assertFalse(any("MAIN_FIELD" in e for e in
                             models.BperpNullParams(main_field_current_ma=25.0).validate(root)))   # 关闭时不校验


if __name__ == "__main__":
    unittest.main()
