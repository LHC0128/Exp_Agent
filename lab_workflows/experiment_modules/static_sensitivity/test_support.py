"""静磁场正式回归共用的假仪器夹具；不会建立真实连接。"""
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import time
import numpy as np

from lockin_amplifier import daq, DAQResult
from ...common import find_project_root, load_mapping
from ...steps import routed_signal_generator
from ...steps.dispersion import acquire_dispersion_point
from ...steps.test_dispersion import FakeHF2 as PointHF2
from ..rf_sensitivity_direct_aw.test_workflow import FakeSignal, FakeHF2
from . import workflow, preparation, excitation, phase, acquisition, analysis_steps
from .models import StaticSensitivityParams


@contextmanager
def fake_rig(root, params=None):
    root = Path(root)
    source = find_project_root()
    (root / "params").mkdir(exist_ok=True)
    for name in ("mapping.yaml", "devices.yaml", "safety_limits.yaml"):
        (root / "params" / name).write_bytes((source / "params" / name).read_bytes())
    mapping = load_mapping(root)
    rig = SimpleNamespace(root=root, mapping=mapping, events=[], generators={}, in_shutdown=False,
        shutdown_error=False, restore_error=False, in_daq=False, xy_angle=0., xy_r=1., fault=None, block_noise=False)
    rig.params = params or replace(StaticSensitivityParams(), dispersion_steps=9,
        noise_duration=0.1, hf2_noise_rate=10000., dispersion_temp_switch_interval_points=3)
    for resource in {mapping[key]["resource"] for keys in preparation.DG_ROUTES.values() for key in keys}:
        rig.generators[resource] = FakeSignal(resource, rig)
    rig.hf2 = FakeHF2(rig, device_id=mapping["lockin_r"]["device_id"])
    rig.gs = Mock()
    rig.gs.get_current.return_value = rig.params.main_magnetic_field / 1000
    def set_range(value):
        rig.gs.get_current_range.return_value = value
    rig.gs.set_current_range.side_effect = set_range
    rig.tec = Mock()
    def point(hf2, **kwargs):
        if rig.fault:
            raise rig.fault
        z = mapping["Z_magnetic_field"]
        voltage = rig.generators[z["resource"]].dc[z["channel"]]
        def raw_factory(index, raw):
            y = analysis_steps._dispersive(voltage, 0.0003, 0.025, 0.007, 0.001)
            for key, value in raw.items():
                value[0]["value"][:] = y if key.endswith(".y") else 1.
            return raw
        fake = PointHF2(rate=hf2.get_double(hf2.demod_path(kwargs["demod_idx"]) + "/rate"), raw_factory=raw_factory)
        fake.clockbase = fake.rate * 1000
        return acquire_dispersion_point(fake, **kwargs)
    def noise(hf2, config, **kwargs):
        if rig.fault:
            raise rig.fault
        kwargs["cancellation_check"]()
        if rig.block_noise:
            (rig.root / "noise_waiting").touch()
            while True:
                kwargs["cancellation_check"]()
                time.sleep(.01)
        signal = np.random.default_rng(42).normal(0, 1e-4, config.grid_cols)
        return [DAQResult(signal_name="sample.y", demod_index=kwargs["demod_idx"],
            values=signal, time=np.arange(signal.size)/kwargs["actual_rate"])]
    shutdown = preparation.safe_shutdown
    def safe(*args, **kwargs):
        rig.in_shutdown = True
        try:
            return shutdown(*args, **kwargs)
        finally:
            rig.in_shutdown = False
    with ExitStack() as stack:
        stack.enter_context(patch.object(workflow, "find_project_root", return_value=root))
        stack.enter_context(patch.object(routed_signal_generator, "create_signal_generator", side_effect=lambda config: rig.generators[config["resource"]]))
        stack.enter_context(patch.object(preparation, "GS200Instrument", return_value=rig.gs))
        stack.enter_context(patch.object(preparation, "TECInstrument", return_value=rig.tec))
        stack.enter_context(patch.object(preparation, "create_lockin", return_value=rig.hf2))
        stack.enter_context(patch.object(preparation, "wait_for_temperature_stable", return_value=rig.params.temperature))
        for module in (preparation, excitation, acquisition):
            if hasattr(module, "wait_for_settle"):
                stack.enter_context(patch.object(module, "wait_for_settle", side_effect=lambda seconds: acquisition.check_cancelled()))
        stack.enter_context(patch.object(acquisition, "acquire_dispersion_point", side_effect=point))
        stack.enter_context(patch.object(daq, "acquire_data", side_effect=noise))
        stack.enter_context(patch.object(preparation, "safe_shutdown", side_effect=safe))
        yield rig
