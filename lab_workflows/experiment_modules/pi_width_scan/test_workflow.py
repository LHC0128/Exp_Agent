"""π 脉冲宽度原子域定标的假仪器回归：相位粗扫、分组扫描写盘、温控门控、取消保留与缺数据占位。

所有用例只用假仪器与临时目录，不连接任何硬件；合成数据由判据模型直接生成。
"""
from __future__ import annotations

import csv
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import yaml

from ... import common
from ...experiment_runtime import WorkflowCancelled
from ...steps import DeviceSession
from . import acquisition, analysis_steps, models, phase_scan, preparation, run_format, stages

ACQUISITION_CASES = {
    "pi-width-scan": {
        "mode": "grouped",
        "cases": {
            "order": ["AcquisitionTest::test_grouped_scan_writes_after_temperature_restore"],
            "slow_disk": ["AcquisitionTest::test_grouped_scan_writes_after_temperature_restore"],
            "group_change": ["AcquisitionTest::test_grouped_scan_writes_after_temperature_restore"],
            "last_group": ["AcquisitionTest::test_grouped_scan_writes_after_temperature_restore"],
            "cancel": ["AcquisitionTest::test_cancel_keeps_completed_points"],
            "acquire_failure": ["AcquisitionTest::test_acquire_failure_restores_and_keeps_prior_points"],
            "partial_save": ["AcquisitionTest::test_cancel_keeps_completed_points",
                             "AcquisitionTest::test_acquire_failure_restores_and_keeps_prior_points",
                             "AcquisitionTest::test_restore_failure_still_saves_group"],
            "restore_failure": ["AcquisitionTest::test_restore_failure_still_saves_group"],
            "save_failure": ["AcquisitionTest::test_save_failure_keeps_point_series"],
        },
        "sources": [
            "lab_workflows/experiment_modules/pi_width_scan/acquisition.py",
            "lab_workflows/experiment_modules/pi_width_scan/phase_scan.py",
            "lab_workflows/experiment_modules/pi_width_scan/preparation.py",
            "lab_workflows/experiment_modules/pi_width_scan/workflow.py",
        ],
    },
}

REGRESSION_CASES = {
    "pi-width-scan": {
        "analysis": {
            "sources": ["lab_workflows/experiment_modules/pi_width_scan/analysis_steps.py"],
            "tests": ["AnalysisTest::test_synthetic_model_recovers_tau_pi",
                      "AnalysisTest::test_poor_fit_is_flagged_with_reason",
                      "AnalysisTest::test_low_signal_is_flagged_with_reason"],
        },
        "parameters": {
            "sources": ["lab_workflows/experiment_modules/pi_width_scan/models.py",
                        "params/experiments/pi-width-scan.yaml"],
            "tests": ["ParamsTest::test_widths_come_from_start_stop_step",
                      "ParamsTest::test_preflight_rejects_width_over_half_period",
                      "ParamsTest::test_rf_phase_grid_and_offset",
                      "ParamsTest::test_preflight_rejects_invalid_phase_range"],
        },
    },
}

CHANNELS = {"z": 1, "y": 2, "trigger": 3, "temp": 4}
TRUE_TAU_PI_US = 5.12
TRUE_TAU0_US = 0.4
TRUE_R_MAX_V = 0.8


def synthetic_r(width_us):
    return TRUE_R_MAX_V * abs(math.sin(math.pi * (width_us - TRUE_TAU0_US) / (2.0 * TRUE_TAU_PI_US)))


class FakeSignals:
    """记录温度开关、脉宽与射频相位的调用顺序；不连接仪器。"""

    def __init__(self):
        self.events = []
        self.temp_on = True
        self.widths = []
        self.current_width = None
        self.burst_phase = {}
        self.burst_state = {}

    def set_output(self, state, channel=None):
        self.events.append(("output", channel, state))

    def set_burst_state(self, state, channel=None):
        self.burst_state[channel] = bool(state)
        self.events.append(("burst", channel, state))

    def set_burst_mode(self, mode, channel=None):
        self.events.append(("burst_mode", channel, mode))

    def set_burst_ncycles(self, count, channel=None):
        pass

    def set_burst_trigger_source(self, source, channel=None):
        pass

    def set_burst_trigger_slope(self, slope, channel=None):
        pass

    def set_burst_phase(self, angle, channel=None):
        self.burst_phase[channel] = float(angle)
        self.events.append(("burst_phase", channel, float(angle)))

    def setup_arbitrary(self, values, freq, amplitude, offset, phase=0.0, channel=None, output=True):
        self.events.append(("arbitrary", channel, freq, amplitude))
        self.widths.append(len(self.events))

    def setup_dc(self, voltage, channel=None):
        if channel == CHANNELS["temp"]:
            self.temp_on = voltage > 0
            self.events.append(("temp", self.temp_on))

    def get_temperature(self, channel=1):
        return 25.0


class FakeDirectory:
    """不落盘的运行目录替身；记录配置更新的时刻是否已恢复温控。"""

    def __init__(self, root, signals):
        self.root = root
        self.raw = root / "raw"
        self.results = root / "results"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.signals = signals
        self.updates = []

    def update_config(self, **updates):
        self.updates.append((self.signals.temp_on, sorted(updates)))
        return updates


class FakeResult:
    def __init__(self, signal_name, time, values):
        self.signal_name, self.time, self.values = signal_name, time, values
        self.config_snapshot = {"acquisition_module": "daq", "requested_rate": 1000.0,
                                "actual_rate": 1000.0, "demod_stream_rate": 1000.0,
                                "actual_duration": 0.2, "time_source": "module_timestamp"}
        self.record_slices = []


class FakeAcquirer:
    """按采集顺序推出当前脉宽，返回判据模型的 R 与固定相位。"""

    def __init__(self, params, signals, *, fail_at=None):
        self.params, self.signals, self.fail_at = params, signals, fail_at
        self.calls = 0

    def __call__(self, device, config=None, demod_idx=0, actual_rate=None, requested_rate=None,
                 timeout=None, cancellation_check=None, strict_cleanup=False):
        self.calls += 1
        if self.fail_at and self.calls == self.fail_at:
            raise RuntimeError("模拟 DAQ 采集失败")
        widths = self.params.widths_us()
        width = widths[min((self.calls - 1) // self.params.n_repeat, len(widths) - 1)]
        size = 10
        time = np.arange(size) / 1000.0
        return [FakeResult("sample.r", time, np.full(size, synthetic_r(width))),
                FakeResult("sample.theta", time, np.full(size, math.radians(30.0)))]


class PhaseFakeAcquirer:
    """R 只随已写入的射频 burst 相位变化：|cos(φ − ψ0)|，ψ0 由构造指定。"""

    def __init__(self, signals, *, psi0_deg=85.5):
        self.signals, self.psi0_deg = signals, psi0_deg

    def __call__(self, device, config=None, demod_idx=0, actual_rate=None, requested_rate=None,
                 timeout=None, cancellation_check=None, strict_cleanup=False):
        phase = float(self.signals.burst_phase[CHANNELS["y"]])
        value = TRUE_R_MAX_V * abs(math.cos(math.radians(phase - self.psi0_deg)))
        time = np.arange(10) / 1000.0
        return [FakeResult("sample.r", time, np.full(10, value)),
                FakeResult("sample.theta", time, np.full(10, math.radians(30.0)))]


class FakeContext:
    def __init__(self, root, params, *, fail_at=None):
        self.root, self.params = root, params
        self.mapping = {"temperature": {"channel": 1}}
        signals = FakeSignals()
        self.devices = {"signals": signals, "channels": dict(CHANNELS), "hf2": SimpleNamespace(device_id="dev1"),
                        "tec": None}
        self.directory = FakeDirectory(root / "run", signals)
        self.state = {"data_files": [], "actual_rates": {"demod0_Sa_s": 1000.0}}
        self.outputs = {}
        self.acquirer = FakeAcquirer(params, signals, fail_at=fail_at)


def fast_params(**overrides):
    p = models.PiWidthScanParams(run_tag="test", n_repeat=2, temp_group_size=3, system_settle_s=0.0,
                                 temp_switch_on_lag_s=0.0, scan_point_duration_s=0.02,
                                 z_pulse_width_start_us=4.0, z_pulse_width_stop_us=6.0,
                                 z_pulse_width_step_us=0.5)
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


def guarded(writer, ctx, name):
    """确认写盘只发生在温控恢复之后。"""
    def wrapper(*args, **kwargs):
        if not ctx.directory.signals.temp_on:
            raise AssertionError(f"{name} 在关温控窗口内写盘")
        ctx.state.setdefault("writes", []).append((name, ctx.directory.signals.temp_on))
        return writer(*args, **kwargs)
    return wrapper


class AcquisitionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    def run_scan(self, ctx, *, series_writer=None, row_writer=None):
        cfg = FakeAcquirer(ctx.params, ctx.devices["signals"], fail_at=ctx.acquirer.fail_at)
        ctx.acquirer = cfg
        patches = [patch.object(acquisition.daq, "acquire_data", cfg)]
        if series_writer:
            patches.append(patch.object(acquisition, "_write_point_series", series_writer))
        if row_writer:
            patches.append(patch.object(acquisition, "_append_scan_row", row_writer))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return acquisition.measure_scan(ctx)

    def read_rows(self, ctx):
        path = ctx.directory.raw / "scan.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_grouped_scan_writes_after_temperature_restore(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        self.run_scan(ctx, series_writer=guarded(acquisition._write_point_series, ctx, "series"),
                      row_writer=guarded(acquisition._append_scan_row, ctx, "row"))
        rows = self.read_rows(ctx)
        total = len(params.widths_us()) * params.n_repeat
        self.assertEqual(len(rows), total)
        self.assertEqual([(row["width_us"], int(row["repeat"])) for row in rows],
                         [(f"{width:g}", repeat) for width in params.widths_us()
                          for repeat in range(params.n_repeat)])
        for width in params.widths_us():
            for repeat in range(params.n_repeat):
                self.assertTrue((ctx.directory.raw / f"R_{width:g}_{repeat}.csv").is_file())
        # 每 TEMP_GROUP_SIZE 个读数关一次温控，组末恢复。
        toggles = [state for kind, *rest in ctx.devices["signals"].events
                   if kind == "temp" for state in rest]
        expected_groups = math.ceil(total / params.temp_group_size)
        # 每次关闭温控后都恢复；退出时再恢复一次，因此关闭次数等于分组数。
        self.assertEqual(toggles[0], False)
        self.assertEqual(toggles[-1], True)
        self.assertEqual(sum(1 for state in toggles if not state), expected_groups)
        self.assertTrue(all(ctx.directory.updates))
        self.assertTrue(all(restored for restored, _ in ctx.directory.updates))
        self.assertEqual(ctx.state["scan_points_completed"], total)

    def test_cancel_keeps_completed_points(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        calls = {"n": 0}

        def cancel_after_three():
            calls["n"] += 1
            if calls["n"] > 3:
                raise WorkflowCancelled("模拟取消")

        with patch.object(acquisition, "check_cancelled", side_effect=cancel_after_three):
            with self.assertRaises(WorkflowCancelled):
                self.run_scan(ctx)
        rows = self.read_rows(ctx)
        self.assertEqual(len(rows), 3)
        self.assertEqual(ctx.devices["signals"].temp_on, True)

    def test_acquire_failure_restores_and_keeps_prior_points(self):
        params = fast_params()
        ctx = FakeContext(self.root, params, fail_at=4)
        with self.assertRaises(RuntimeError):
            self.run_scan(ctx)
        rows = self.read_rows(ctx)
        self.assertEqual(len(rows), params.temp_group_size)
        self.assertTrue(ctx.devices["signals"].temp_on)

    def test_restore_failure_still_saves_group(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        original = acquisition.set_temperature_switch
        calls = {"n": 0}

        def flaky(device, enabled, **kwargs):
            if enabled:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("模拟恢复温控失败")
            return original(device, enabled, **kwargs)

        with patch.object(acquisition, "set_temperature_switch", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self.run_scan(ctx)
        rows = self.read_rows(ctx)
        self.assertEqual(len(rows), params.temp_group_size)
        self.assertTrue(ctx.devices["signals"].temp_on)

    def test_save_failure_keeps_point_series(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)

        def broken_row(*args, **kwargs):
            raise OSError("模拟磁盘写入失败")

        with self.assertRaises(OSError):
            self.run_scan(ctx, row_writer=broken_row)
        self.assertTrue(ctx.devices["signals"].temp_on)
        self.assertTrue((ctx.directory.raw / "R_4_0.csv").is_file())
        self.assertFalse((ctx.directory.raw / "scan.csv").exists())

    def read_phase_rows(self, ctx):
        path = ctx.directory.raw / "phase_scan.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def run_phase_scan(self, ctx, acquirer=None):
        with patch.object(acquisition.daq, "acquire_data", acquirer or PhaseFakeAcquirer(ctx.devices["signals"])):
            acquisition.measure_phase_scan(ctx)

    def test_phase_scan_stage_writes_curve_and_chooses_peak(self):
        # 0–180°、步进 22.5°，共 9 个角度，每角度 3 次。
        params = fast_params()
        ctx = FakeContext(self.root, params)
        self.run_phase_scan(ctx)
        rows = self.read_phase_rows(ctx)
        self.assertEqual(len(rows), 9 * params.y_rf_phase_repeat)
        self.assertEqual([row["phase_deg"] for row in rows[::params.y_rf_phase_repeat]],
                         [f"{angle:g}" for angle in params.rf_phase_angles_deg()])
        record = ctx.state["phase_check"]
        self.assertTrue(record["enabled"])
        self.assertEqual(record["chosen_deg"], 90.0)
        # 峰在内部：抛物线精化落在 67.5° 与 112.5° 之间，且贴近真实零点 85.5°
        # （三点抛物线只是 |cos| 的局部近似，允许 1° 内偏差）。
        self.assertGreater(record["refined_deg"], 67.5)
        self.assertLess(record["refined_deg"], 112.5)
        self.assertAlmostEqual(record["refined_deg"], 85.5, delta=1.0)
        self.assertEqual(len(record["R_curve"]), 9)
        self.assertAlmostEqual(record["R_chosen"],
                               TRUE_R_MAX_V * math.cos(math.radians(4.5)), places=9)
        self.assertEqual(ctx.state["phase_source"], "stage")
        self.assertTrue((ctx.directory.results / "phase_vs_phase.png").is_file())
        # 只跑 A03 时只产出相位曲线，不写扫描表。
        self.assertFalse((ctx.directory.raw / "scan.csv").exists())
        self.assertFalse((ctx.directory.raw / "R_4_0.csv").exists())

    def test_rf_phase_change_follows_direct_aw_order(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        self.run_phase_scan(ctx)
        events = [event for event in ctx.devices["signals"].events if event[1] == CHANNELS["y"]]
        # 每个角度都按 DirectAW 校相顺序：关 output → 关 burst → 写相位 → 开 burst → 开 output。
        self.assertEqual(events[:5], [("output", CHANNELS["y"], False), ("burst", CHANNELS["y"], False),
                                      ("burst_phase", CHANNELS["y"], params.rf_phase_angles_deg()[0]),
                                      ("burst", CHANNELS["y"], True), ("output", CHANNELS["y"], True)])
        phases = [event[2] for event in events if event[0] == "burst_phase"]
        self.assertEqual(phases, params.rf_phase_angles_deg())

    def test_phase_scan_disabled_skips_stage(self):
        params = fast_params(phase_check_enabled=False)
        ctx = FakeContext(self.root, params)
        self.run_phase_scan(ctx)
        self.assertFalse((ctx.directory.raw / "phase_scan.csv").exists())
        self.assertFalse((ctx.directory.results / "phase_vs_phase.png").exists())
        self.assertEqual(ctx.state["phase_check"], {"enabled": False})
        self.assertEqual(ctx.state["phase_source"], "parameter")

    def test_scan_uses_parameter_phase_without_phase_stage(self):
        # 只跑 A04（未跑 A03）：状态里没有 phase_check，回落到 Y_RF_PHASE_DEG。
        params = fast_params()
        ctx = FakeContext(self.root, params)
        self.run_scan(ctx)
        self.assertEqual(ctx.state["phase_source"], "parameter")
        self.assertEqual(ctx.state["phase_check"], {"enabled": False})
        self.assertEqual(ctx.state["rf_phase_deg"], params.y_rf_phase_deg)
        self.assertEqual(ctx.devices["signals"].burst_phase[CHANNELS["y"]], params.y_rf_phase_deg)

    def test_scan_prefers_phase_stage_refined_angle(self):
        params = fast_params()
        ctx = FakeContext(self.root, params)
        ctx.state["phase_check"] = dict(enabled=True, chosen_deg=90.0, refined_deg=85.5,
                                        R_chosen=0.79, R_curve=[])
        self.run_scan(ctx)
        self.assertEqual(ctx.state["phase_source"], "stage")
        self.assertEqual(ctx.state["rf_phase_deg"], 85.5)
        self.assertEqual(ctx.devices["signals"].burst_phase[CHANNELS["y"]], 85.5)


class PreparationTest(unittest.TestCase):
    """A02/A04 的接线与安全收尾：全部设备用假对象，不打开任何通信。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copytree(Path(common.__file__).resolve().parents[1] / "params", self.root / "params")

    class FakeDevice:
        def __init__(self, label):
            self.label, self.connected, self.disconnected = label, False, False

        def connect(self):
            self.connected = True

        def disconnect(self):
            self.disconnected = True

    def fake_devices(self):
        outer = self

        class FakeDG(outer.FakeDevice):
            def __init__(self):
                super().__init__("dg")
                self.clock, self.calls, self.outputs = "INT", [], {}

            def get_ref_clock_source(self):
                return self.clock

            def set_ref_clock_source(self, source):
                self.calls.append(("clock", source))
                self.clock = "EXT" if source.upper().startswith("EXT") else "INT"

            def set_output(self, state, channel=None):
                self.outputs[channel] = state

            def set_output_load(self, load, channel=None):
                self.calls.append(("load", channel, load))

            def set_burst_state(self, state, channel=None):
                self.calls.append(("burst", state))

            def set_burst_mode(self, mode, channel=None):
                self.calls.append(("burst_mode", channel, mode))

            def set_burst_ncycles(self, count, channel=None):
                self.calls.append(("burst_ncycles", count))

            def set_burst_trigger_source(self, source, channel=None):
                self.calls.append(("burst_source", source))

            def set_burst_trigger_slope(self, slope, channel=None):
                self.calls.append(("burst_slope", slope))

            def set_burst_phase(self, angle, channel=None):
                self.calls.append(("burst_phase", channel, angle))

            def set_mod_state(self, state, channel=None):
                pass

            def setup_dc(self, voltage, channel=None):
                self.calls.append(("dc", channel, voltage))

            def setup_sine(self, freq, amplitude, offset=0.0, phase=0.0, channel=None):
                self.calls.append(("sine", channel, freq, amplitude))

            def setup_square(self, freq, amplitude, offset=0.0, dcycle=50.0, phase=0.0, channel=None):
                self.calls.append(("square", channel, freq, amplitude, dcycle))

            def phase_init(self, channel=None):
                pass

            def set_sync_state(self, state, channel=None):
                self.calls.append(("sync", state))

        class FakeSignals:
            def __init__(self, device):
                self.device = device

            def iter_physical_routes(self):
                return (SimpleNamespace(mapping_key="Z_magnetic_field", device=self.device),)

            def __getattr__(self, name):
                def call(*args, **kwargs):
                    kwargs.pop("channel", None)
                    return getattr(self.device, name)(*args, **kwargs)
                return call

        class FakeLockin(outer.FakeDevice):
            model, device_id, acquisition_mode = "MFLI", "dev1", "auto"
            # 解调传输率初值：100 kSa/s 超过自动选模阈值（70 kSa/s），走解调 Scope，不重写该速率。
            stream_rate = 1000.0
            capabilities = {"model": "MFLI", "options": ["MF-MD"], "demodulators": 4, "oscillators": 4,
                            "voltage_inputs": 1, "signal_outputs": 1, "aux_outputs": 4,
                            "max_frequency_hz": 5e6, "scope_channels": 1, "scope_max_samples": 2048,
                            "scope_max_rate": 60e6, "aux_input_feedback_verified": False}

            def __init__(self):
                super().__init__("mfli")
                self.values, self.extclk = {}, True
                self.values[f"{self.demod_path(0)}/rate"] = self.stream_rate

            def demod_path(self, idx=0):
                return f"/dev1/demods/{idx}"

            def sig_in_path(self, idx=0):
                return f"/dev1/sigins/{idx}"

            def osc_path(self, idx=0):
                return f"/dev1/oscs/{idx}"

            def get_extclk(self):
                return self.extclk

            def set_extclk(self, enabled):
                self.extclk = bool(enabled)

            def set_double(self, path, value):
                self.values[path] = float(value)

            def set_int(self, path, value):
                self.values[path] = int(value)

            def get_double(self, path):
                return float(self.values.get(path, 1000.0))

            def get_int(self, path):
                return int(self.values.get(path, 0))

            def sync(self):
                pass

        class FakeAmp(outer.FakeDevice):
            def __init__(self):
                super().__init__("amp")
                self.output, self.settings = None, {}

            def set_output(self, state):
                self.output = state

            def set_gain_db(self, value):
                self.settings["gain_db"] = value

            def set_input_impedance(self, value):
                self.settings["input_impedance_ohms"] = value

            def set_output_impedance(self, value):
                self.settings["output_impedance_ohms"] = value

        class FakeScope(outer.FakeDevice):
            def __init__(self):
                super().__init__("scope")
                self.stopped = False

            def trigger_stop(self):
                self.stopped = True

        class FakeGS(outer.FakeDevice):
            def __init__(self):
                super().__init__("gs")
                self.output, self.current, self.source_function = True, 0.0005, "CURRent"
                self.current_range, self.current_limit = 0.001, 0.02

            def get_source_function(self):
                return self.source_function

            def set_source_function(self, value):
                self.source_function = value

            def get_current(self):
                return self.current

            def set_current(self, value):
                self.current = value

            def get_output(self):
                return self.output

            def set_output(self, state):
                self.output = state

            def get_current_range(self):
                return self.current_range

            def set_current_range(self, value):
                self.current_range = value

            def get_current_limit(self):
                return self.current_limit

            def set_current_limit(self, value):
                self.current_limit = value

        class FakeTec(outer.FakeDevice):
            def __init__(self):
                super().__init__("tec")

            def get_temperature(self, channel=1):
                return 25.0

        return dict(dg=FakeDG(), signals=FakeSignals, lockin=FakeLockin(), amp=FakeAmp(),
                    scope=FakeScope(), gs=FakeGS(), tec=FakeTec())

    def build_context(self, params):
        fakes = self.fake_devices()
        dg = fakes["dg"]

        def routes(session, name, bindings):
            fake_signals = fakes["signals"](dg)
            session.bind(name, fake_signals)
            return fake_signals, {key: index + 1 for index, key in enumerate(bindings)}

        context = SimpleNamespace(
            root=self.root, params=params, devices={}, outputs={},
            device_session=DeviceSession(), state=run_format.initial_metadata(params),
            mapping=None, main_field_state=None)
        patches = [
            patch.object(preparation, "connect_signal_generator_routes", side_effect=routes),
            patch.object(preparation, "create_lockin", side_effect=lambda config: fakes["lockin"]),
            patch.object(preparation, "Aigtek1200BInstrument", side_effect=lambda *a, **k: fakes["amp"]),
            patch.object(preparation, "create_oscilloscope", side_effect=lambda config: fakes["scope"]),
            patch.object(preparation, "GS200Instrument", side_effect=lambda *a, **k: fakes["gs"]),
            patch.object(preparation, "TECInstrument", side_effect=lambda *a, **k: fakes["tec"]),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        context.mapping = preparation.preflight(params, self.root)[1]
        return context, fakes

    def test_connect_configure_and_shutdown_restore_every_output(self):
        params = fast_params()
        ctx, fakes = self.build_context(params)
        preparation.connect(ctx)
        self.assertEqual(ctx.mapping["lockin_r"]["model"], "MFLI")
        clocks = preparation.clocks(ctx)
        self.assertTrue(all(record["ok"] for record in clocks.values()))
        self.assertTrue(all(record["target"] == "EXT" for record in clocks.values()))
        preparation.disable_main_field(ctx)
        self.assertFalse(fakes["gs"].output)
        self.assertFalse(ctx.state["main_field"]["output_after"])
        preparation.configure_chain(ctx)
        self.assertEqual(fakes["amp"].output, False)
        self.assertEqual(fakes["amp"].settings["gain_db"], params.amplifier_gain_db)
        preparation.configure_lockin(ctx)
        # 100 kSa/s 超过 MFLI 自动选模阈值：走解调 Scope，传输率保持原值，参数只记录请求值。
        self.assertEqual(ctx.state["lockin"]["requested_rate_Sa_s"], params.demod0_rate_sa_s)
        self.assertEqual(ctx.state["actual_rates"]["demod0_Sa_s"], fakes["lockin"].stream_rate)
        self.assertEqual(ctx.state["lockin"]["time_constant_s"], params.demod0_tc_s)
        self.assertEqual(ctx.state["lockin"]["order"], params.demod0_order)
        preparation.configure_rf(ctx)
        preparation.configure_trigger(ctx)
        preparation.configure_z_idle(ctx)
        preparation.enable_temperature_switch(ctx)
        self.assertTrue(any(call[0] == "square" and call[2] == params.y_rf_freq_hz for call in fakes["dg"].calls))
        self.assertTrue(any(call[0] == "sine" and call[2] == params.y_rf_freq_hz for call in fakes["dg"].calls))
        action_errors, disconnect_errors = preparation.shutdown(ctx)
        self.assertEqual((action_errors, disconnect_errors), ((), ()))
        self.assertTrue(fakes["gs"].output)
        self.assertEqual(fakes["amp"].output, False)
        self.assertTrue(fakes["scope"].stopped)
        # 收尾把 Z 通道归零并关输出，随后恢复温控（温控开关输出保持 ON）。
        self.assertIn(("dc", None, 0.0), fakes["dg"].calls)
        self.assertIs(fakes["dg"].outputs[None], True)
        self.assertTrue(ctx.outputs["Z 通道"].startswith("DC 0 V"))
        self.assertTrue(fakes["dg"].disconnected)
        self.assertTrue(fakes["lockin"].disconnected)
        self.assertTrue(ctx.outputs["温控"].startswith("ON"))

    def test_main_field_must_read_back_off(self):
        params = fast_params()
        ctx, fakes = self.build_context(params)
        preparation.connect(ctx)
        fakes["gs"].set_output = lambda state: None  # 设备拒绝关闭
        with self.assertRaises(preparation.SafetyAbort):
            preparation.disable_main_field(ctx)


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.run_dir = Path(self.temporary.name) / "run"
        (self.run_dir / "raw").mkdir(parents=True)
        self.params = fast_params()
        config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
                      schema_version=self.params.schema_version,
                      raw_format_version=run_format.RAW_FORMAT_VERSION,
                      completion_status="completed", parameters=self.params.to_external())
        (self.run_dir / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    def write_scan(self, rows):
        with (self.run_dir / "raw" / "scan.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(acquisition.SCAN_FIELDS))
            writer.writerows(rows)

    def write_phase_check_config(self, record):
        """把相位自检结果写进运行配置，模拟 A03 落盘后的 experiment_config.yaml。"""
        path = self.run_dir / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["phase_check"] = record
        path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def test_synthetic_model_recovers_tau_pi(self):
        rows = []
        for width in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            value = synthetic_r(width)
            for repeat in range(5):
                rows.append([width, repeat, f"{value:.9g}", "30.0", "2026-01-01T00:00:00+08:00",
                             "0.05", "5000", "190", "25.0"])
        self.write_scan(rows)
        self.write_phase_check_config(dict(enabled=True, chosen_deg=90.0, refined_deg=85.5,
                                           R_chosen=0.8, R_curve=[]))
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["rows"], 35)
        self.assertAlmostEqual(summary["tau_pi_us"], TRUE_TAU_PI_US, places=4)
        self.assertAlmostEqual(summary["fit"]["tau0_us"], TRUE_TAU0_US, places=3)
        self.assertAlmostEqual(summary["fit"]["r_max_v"], TRUE_R_MAX_V, places=4)
        self.assertAlmostEqual(summary["ratio_to_electrical"], TRUE_TAU_PI_US / 5.122, places=6)
        self.assertEqual(summary["electrical_pi_width_us"], 5.122)
        self.assertEqual(summary["fit_quality"], "good")
        self.assertTrue(summary["keep"])
        self.assertTrue(all(item["keep"] for item in summary["points"]))
        # 摘要记录实际生效的射频相位（优先精化值）、时间偏移、来源与相位曲线。
        self.assertEqual(summary["rf_phase_deg"], 85.5)
        self.assertAlmostEqual(summary["rf_phase_offset_us"], 85.5 / 360.0 * 200.0, places=9)
        self.assertEqual(summary["phase_source"], "stage")
        self.assertEqual(summary["phase_check"]["chosen_deg"], 90.0)
        self.assertIsNone(summary["demod0_phase_deg"])
        self.assertNotIn("signal_low", summary)
        self.assertTrue((self.run_dir / "results" / "scan_summary.json").is_file())
        self.assertTrue((self.run_dir / "results" / "r_vs_width.png").is_file())

    def test_poor_fit_is_flagged_with_reason(self):
        rows = []
        for width in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            # 三角波而不是判据模型：标准误很小，残差必然远超 3 倍中位标准误。
            value = TRUE_R_MAX_V * (1.0 - abs(width - 5.5) / 4.0)
            for repeat in range(5):
                rows.append([width, repeat, f"{value + 1e-5 * repeat:.9g}", "30.0",
                             "2026-01-01T00:00:00+08:00", "0.05", "5000", "190", "25.0"])
        self.write_scan(rows)
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["fit_quality"], "poor")
        self.assertIn("残差 RMS", summary["fit_quality_reason"])
        self.assertFalse(summary["keep"])
        # 运行配置没有相位粗扫记录时按关闭记录，射频相位回落到参数值（默认 90°）。
        self.assertEqual(summary["phase_check"], {"enabled": False})
        self.assertEqual(summary["phase_source"], "parameter")
        self.assertEqual(summary["rf_phase_deg"], 90.0)

    def test_low_signal_is_flagged_with_reason(self):
        rows = []
        for width in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            # R 只有噪声量级（~1 µV），拟合幅度远小于单点标准误中位数的 5 倍。
            for repeat, delta in enumerate((-2, -1, 0, 1, 2)):
                rows.append([width, repeat, f"{1e-6 + delta * 1e-6:.9g}", "30.0",
                             "2026-01-01T00:00:00+08:00", "0.05", "5000", "190", "25.0"])
        self.write_scan(rows)
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertTrue(summary["signal_low"])
        self.assertIn("射频相位落在零点附近", summary["signal_low_reason"])

    def test_missing_scan_writes_placeholder(self):
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["rows"], 0)
        self.assertIn("raw/scan.csv", summary["reason"])
        self.assertEqual(summary["fit_quality"], "unavailable")
        self.assertTrue((self.run_dir / "results" / "r_vs_width.png").is_file())

    def test_too_few_widths_writes_reason(self):
        rows = [[4.0, repeat, "0.1", "0.0", "2026-01-01T00:00:00+08:00", "0.05", "5000", "190", "25.0"]
                for repeat in range(5)]
        self.write_scan(rows)
        _, params = analysis_steps.load_config(self.run_dir)
        summary = analysis_steps.scan_report(self.run_dir, params)
        self.assertEqual(summary["widths"], 1)
        self.assertIn("少于拟合所需", summary["reason"])
        self.assertTrue((self.run_dir / "results" / "scan_summary.json").is_file())


class ParamsTest(unittest.TestCase):
    def test_widths_come_from_start_stop_step(self):
        p = models.PiWidthScanParams()
        self.assertEqual(p.widths_us(), [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0])
        self.assertAlmostEqual(p.pulse_period_us, 200.0)
        self.assertAlmostEqual(p.pulse_half_period_us, 100.0)
        q = models.PiWidthScanParams(z_pulse_width_stop_us=6.2, z_pulse_width_step_us=0.5)
        self.assertEqual(q.widths_us(), [4.0, 4.5, 5.0, 5.5, 6.0])

    def test_preflight_rejects_width_over_half_period(self):
        p = models.PiWidthScanParams(z_pulse_width_stop_us=120.0, z_pulse_width_step_us=20.0)
        self.assertTrue(any("半个脉冲周期" in error for error in p.validate()))

    def test_preflight_rejects_width_below_twice_edge(self):
        p = models.PiWidthScanParams(z_pulse_width_start_us=1.0, z_pulse_width_stop_us=7.0)
        self.assertTrue(any("两倍沿宽" in error for error in p.validate()))

    def test_preflight_rejects_empty_or_inverted_grid(self):
        p = models.PiWidthScanParams(z_pulse_width_start_us=7.0, z_pulse_width_stop_us=4.0)
        self.assertTrue(any("不能小于" in error for error in p.validate()))

    def test_predicted_width_uses_coil_constant(self):
        p = models.PiWidthScanParams()
        self.assertAlmostEqual(p.predicted_width_us(), 1.0 + 1e6 / (2 * 653.79 * 190.0), places=9)

    def test_rf_phase_grid_and_offset(self):
        p = models.PiWidthScanParams()
        # 默认 0–180°、步进 22.5°，共 9 个角度（|cos| 的周期是 180°，另一半重复）。
        self.assertEqual(p.rf_phase_angles_deg(), [0.0, 22.5, 45.0, 67.5, 90.0, 112.5, 135.0, 157.5, 180.0])
        self.assertAlmostEqual(p.rf_phase_offset_us(), p.pulse_period_us / 4.0, places=9)
        self.assertAlmostEqual(p.rf_phase_offset_us(1.0), p.pulse_period_us / 360.0, places=9)
        # 步进除不尽时补上终点，保持两端都在。
        q = models.PiWidthScanParams(y_rf_phase_stop_deg=100.0)
        self.assertEqual(q.rf_phase_angles_deg()[-1], 100.0)
        self.assertEqual(len(q.rf_phase_angles_deg()), 6)
        self.assertAlmostEqual(p.phase_check_width_us(), 5.0)

    def test_preflight_rejects_invalid_phase_range(self):
        zero = models.PiWidthScanParams(y_rf_phase_step_deg=0.0)
        self.assertTrue(any("Y_RF_PHASE_STEP_DEG 必须为正" in error for error in zero.validate()))
        inverted = models.PiWidthScanParams(y_rf_phase_start_deg=100.0, y_rf_phase_stop_deg=20.0)
        self.assertTrue(any("不能小于" in error for error in inverted.validate()))
        out_of_range = models.PiWidthScanParams(y_rf_phase_deg=400.0)
        self.assertTrue(any("0–360" in error for error in out_of_range.validate()))
        reference = models.PiWidthScanParams(reference_pi_width_us=0.0)
        self.assertTrue(any("REFERENCE_PI_WIDTH_US" in error for error in reference.validate()))


class StagesTest(unittest.TestCase):
    def test_stage_ids_and_independent_selection(self):
        by_id = {stage.id: stage for stage in stages.ACQUISITION}
        self.assertEqual(list(by_id), ["A01", "A02", "A03", "A04", "A05"])
        self.assertEqual((stages.RUN_ID, stages.FINISH_ID), ("A04", "A05"))
        # A03（相位粗扫）与 A04（扫脉宽）都只依赖 A02，因此可以各自单独勾选。
        self.assertEqual(by_id["A03"].dependencies, ("A02",))
        self.assertEqual(by_id["A04"].dependencies, ("A02",))
        self.assertEqual(by_id["A03"].artifacts, ("raw/phase_scan.csv", "results/phase_vs_phase.png"))

    def test_phase_scan_peak_math(self):
        points = [dict(phase_deg=0.0, r_mean_v=0.1), dict(phase_deg=22.5, r_mean_v=0.9),
                  dict(phase_deg=45.0, r_mean_v=0.2)]
        record = phase_scan.choose_peak(points)
        self.assertEqual((record["chosen_deg"], record["R_chosen"]), (22.5, 0.9))
        self.assertGreater(record["refined_deg"], 0.0)
        self.assertLess(record["refined_deg"], 45.0)
        # 端点峰不做精化；相邻三点共线时也没有顶点。
        self.assertIsNone(phase_scan.choose_peak([dict(phase_deg=0.0, r_mean_v=0.9),
                                                  dict(phase_deg=22.5, r_mean_v=0.2)])["refined_deg"])
        self.assertIsNone(phase_scan.parabolic_peak(0.0, 0.0, 1.0, 1.0, 2.0, 2.0))


if __name__ == "__main__":
    unittest.main()
