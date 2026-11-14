"""真实编排、真实文件与假仪器：一阶 RL 回路模拟线圈电流，不连接硬件。"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from oscilloscope import AcquisitionResult

from ...common import WorkflowCancelled
from ...experiment_params import ExperimentParams
from . import acquisition, analysis, preparation, stages, workflow
from .models import CoilDriveCharacterizationParams
from .preparation import SafetyAbort

ACQUISITION_CASES = {
    "coil-drive-characterization": {"mode": "single", "cases": {
        "order": ["test_full_run_then_analysis_recovers_circuit"],
        "cancel": ["test_cancel_mid_sweep_shuts_down_and_keeps_frames"],
        "acquire_failure": ["test_gain_above_declared_aborts_with_shutdown"],
        "partial_save": ["test_cancel_mid_sweep_shuts_down_and_keeps_frames"],
        "entrypoints": ["test_stepwise_rerun_copies_zero_frames_to_new_directory"],
    }, "sources": [
        "lab_workflows/experiment_modules/coil_drive_characterization/preparation.py",
        "lab_workflows/experiment_modules/coil_drive_characterization/acquisition.py",
        "lab_workflows/experiment_modules/coil_drive_characterization/workflow.py",
        "lab_workflows/experiment_modules/coil_drive_characterization/session.py",
    ]},
}
REGRESSION_CASES = {
    "coil-drive-characterization": {"parameters": {
        "sources": ["lab_workflows/experiment_modules/coil_drive_characterization/models.py",
                    "params/experiments/coil-drive-characterization.yaml"],
        "tests": ["test_default_yaml_round_trip", "test_preflight_rejects_unsafe_or_inconsistent_plans"],
    }},
}
ROOT = Path(__file__).resolve().parents[3]
TAU = 2e-6  # L/R：一阶带宽约 80 kHz


def copy_config(root, *, drop_current_limit=False):
    (root / "params").mkdir(parents=True, exist_ok=True)
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml", "clock_sources.yaml"):
        if (ROOT / "params" / name).exists():
            shutil.copyfile(ROOT / "params" / name, root / "params" / name)
    if drop_current_limit:
        path = root / "params" / "safety_limits.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["safety_limits"].pop("z_pulse_coil_current")
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def small_params(**changes):
    base = replace(CoilDriveCharacterizationParams(), frequency_start_hz=1e3, frequency_stop_hz=1e6,
                   points_per_decade=2, pulse_widths_us=[5.0, 20.0, 100.0], zero_frames=2, settle_s=0.0)
    return replace(base, **changes)


class Rig:
    def __init__(self, *, true_gain=10.0, offset_a=2e-3, sync_peak_v=3.3):
        self.events, self.true_gain, self.offset_a = [], true_gain, offset_a
        self.sync_peak_v = sync_peak_v
        self.amp_on, self.mode, self.reads, self.cancel_at = False, ("dc", 0.0), 0, None
        self.drop_sync_frames = 0
        self.pulse_width_s = 0.0
        self.pulse_leading_s = 100e-9
        self.pulse_width_clamp_s = None
        # 假 1200B 的写入值：预测电流与假电流都按它计算，与真机“写入即生效、无回读”一致。
        self.amp_settings = {"gain_db": 20.0, "input_impedance_ohm": 1_000_000.0, "output_impedance_ohm": 50.0}
        self.source_load = None
        rig = self

        class Source:
            channel = 1
            clock = "INT"
            def connect(self): rig.events.append(("sg_connect",))
            def disconnect(self): rig.events.append(("sg_disconnect",))
            def get_ref_clock_source(self): return self.clock
            def set_ref_clock_source(self, value): self.clock = value
            def set_burst_state(self, state, channel=None): rig.events.append(("sg_burst", state))
            def set_mod_state(self, state, channel=None): rig.events.append(("sg_mod", state))
            def set_sync_state(self, state, channel=None): rig.events.append(("sg_sync", state))
            def set_output(self, state, channel=None): rig.events.append(("sg_output", state))
            def set_output_load(self, ohms, channel=None):
                rig.source_load = ohms; rig.events.append(("sg_load", ohms))
            def setup_dc(self, volts, channel=None):
                rig.mode = ("dc", volts); rig.events.append(("sg_dc", volts))
            def setup_sine(self, f, vpp, offset, channel=None):
                rig.mode = ("sine", f, vpp); rig.events.append(("sg_sine", f))
            def setup_pulse(self, f, amp, offset, width=None, channel=None):
                rig.mode = ("pulse", amp, width); rig.events.append(("sg_pulse", width))
                # 假信号源默认如实执行；pulse_width_clamp_s 模拟 DG 的最小脉宽钳位。
                rig.pulse_width_s = max(width, rig.pulse_width_clamp_s) if rig.pulse_width_clamp_s else width
            def get_pulse_width(self, channel=None): return rig.pulse_width_s
            def get_pulse_leading(self, channel=None): return rig.pulse_leading_s
            def get_pulse_trailing(self, channel=None): return rig.pulse_leading_s

        class Amp:
            def __init__(self, *args, safety_check=None, **kwargs): self.check = safety_check
            def connect(self): rig.events.append(("amp_connect",))
            def disconnect(self): rig.events.append(("amp_disconnect",))
            def set_output(self, enabled):
                if enabled:
                    self.check("output", True)
                rig.amp_on = enabled; rig.events.append(("amp_output", enabled))
            def set_gain_db(self, gain_db):
                self.check("gain_db", gain_db)
                rig.amp_settings["gain_db"] = gain_db; rig.events.append(("amp_gain", gain_db))
            def set_input_impedance(self, ohms):
                self.check("input_impedance_ohms", ohms)
                rig.amp_settings["input_impedance_ohm"] = ohms; rig.events.append(("amp_input_ohm", ohms))
            def set_output_impedance(self, ohms):
                self.check("output_impedance_ohms", ohms)
                rig.amp_settings["output_impedance_ohm"] = ohms; rig.events.append(("amp_output_ohm", ohms))

        class Scope:
            def connect(self): rig.events.append(("scope_connect",))
            def disconnect(self): rig.events.append(("scope_disconnect",))
            def trigger_stop(self): rig.events.append(("scope_stop",))

        class Acquirer:
            depth_cap = None

            def use_auto_depth(self, max_points):
                self.depth_cap = max_points

            def acquire_all(self, config):
                rig.reads += 1
                if rig.cancel_at is not None and rig.reads >= rig.cancel_at:
                    raise WorkflowCancelled("模拟取消")
                assert self.depth_cap == 1_000_000  # 实验把最大存储深度参数交给采集器
                n = 10_000  # 假示波器的 AUTO 点数
                rate = n / config.sampling_time
                t = (np.arange(n) - n / 2) / rate
                channels = {c.number: c for c in config.channels}
                sense = np.clip(rig.current(t) * 10.0 + np.random.default_rng(rig.reads).normal(0, 1e-5, n),
                                -4 * channels[2].scale, 4 * channels[2].scale)
                snap = {"requested": config.to_dict()}
                peak = rig.sync_peak_v
                if config.trigger.mode == "NORMal" and rig.drop_sync_frames:
                    rig.drop_sync_frames -= 1
                    peak = 0.1  # 模拟 DG4000 同步锁存偶发失败：该帧没有同步沿
                return [AcquisitionResult(2, np.array([], np.int16), sense, t, {}, snap),
                        AcquisitionResult(3, np.array([], np.int16), np.where(t >= 0, peak, 0.0), t, {}, snap)]

        self.Source, self.Amp, self.Scope, self.Acquirer = Source, Amp, Scope, Acquirer

    def current(self, t):
        if not self.amp_on:
            return np.zeros_like(t)
        # 回路 = 采样 10 Ω + 阻尼 10 Ω（small_params 默认线圈 0 Ω）+ 功放输出阻抗档位。
        k = self.true_gain / (20.0 + self.amp_settings["output_impedance_ohm"])
        if self.mode[0] == "dc":
            base = np.full_like(t, k * self.mode[1])
        elif self.mode[0] == "sine":
            _, f, vpp = self.mode
            h = 1.0 / complex(1.0, 2 * math.pi * f * TAU)
            base = k * vpp / 2 * abs(h) * np.cos(2 * math.pi * f * t + np.angle(h))
        else:
            _, amp, width = self.mode
            base = np.zeros_like(t)
            on, after = (t >= 0) & (t < width), t >= width
            base[on] = 1 - np.exp(-t[on] / TAU)
            base[after] = (1 - math.exp(-width / TAU)) * np.exp(-(t[after] - width) / TAU)
            base *= k * amp
        return base + self.offset_a


@pytest.fixture
def rig(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig()
    install(monkeypatch, value)
    return value


def install(monkeypatch, value):
    monkeypatch.setattr(preparation, "create_signal_generator", lambda cfg: value.Source())
    monkeypatch.setattr(preparation, "create_oscilloscope", lambda cfg: value.Scope())
    monkeypatch.setattr(preparation, "create_scope_acquisition", lambda scope, **kw: value.Acquirer())
    monkeypatch.setattr(preparation, "Aigtek1200BInstrument", value.Amp)
    monkeypatch.setattr(preparation, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(acquisition, "sleep", lambda *a, **k: None)


def assert_shutdown_order(events):
    names = [e[0] if len(e) == 1 else e for e in events]
    amp_off = max(i for i, e in enumerate(names) if e == ("amp_output", False))
    dg_off = max(i for i, e in enumerate(names) if e == ("sg_output", False))
    assert amp_off < dg_off
    assert ("sg_sync", False) in names[amp_off:] and ("sg_dc", 0.0) in names[amp_off:]
    assert {"amp_disconnect", "scope_disconnect", "sg_disconnect"} <= set(names[dg_off:])


def read_config(directory):
    return yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))


def test_full_run_then_analysis_recovers_circuit(tmp_path, rig):
    directory = workflow.run(small_params(), project_root=tmp_path)
    config = read_config(directory)
    assert config["completion_status"] == "completed" and config["safety_shutdown"]["completed"]
    assert config["gain_check"]["ratio"] == pytest.approx(1.0, rel=0.02)
    names = sorted(p.name for p in (directory / "raw").glob("*.npz"))
    assert names[:2] == ["gain_check.npz", "pulse_00.npz"] and len(names) == 1 + 3 + 7 + 4
    # 功放开启发生在信号源 0 V 之后；关断功放早于信号源关闭输出。
    assert rig.events.index(("amp_output", True)) > rig.events.index(("sg_dc", 0.0))
    assert_shutdown_order(rig.events)

    summary = analysis.analyze(directory)
    assert summary["status"] == "completed"
    assert summary["zero_offset"]["offset_current_a"] == pytest.approx(2e-3, abs=2e-5)
    # 每十倍频 2 点的对数插值误差约 10%，只核对量级与位置。
    assert summary["bandwidth_3db_hz"] == pytest.approx(1 / (2 * math.pi * TAU), rel=0.15)
    # 低频 |I/V| = 实际增益 ÷ (采样 10 Ω + 阻尼 10 Ω + 功放输出阻抗)。
    expected_low = rig.true_gain / (20.0 + rig.amp_settings["output_impedance_ohm"])
    assert summary["transfer"][0]["admittance_a_per_v"] == pytest.approx(expected_low, rel=0.01)
    assert summary["rise_10_90_s"] == pytest.approx(TAU * math.log(9), rel=0.05)
    assert summary["shortest_usable_pulse_width_s"] == pytest.approx(20e-6)
    assert (directory / "results" / "coil_drive.png").is_file()


def test_gain_above_declared_aborts_with_shutdown(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig(true_gain=20.0)  # 实际增益 26 dB，参数写入 20 dB
    install(monkeypatch, value)
    with pytest.raises(SafetyAbort, match="核对功放增益与阻抗是否生效"):
        workflow.run(small_params(), project_root=tmp_path)
    directory = next((tmp_path / "data" / "coil_drive_characterization").iterdir())
    config = read_config(directory)
    assert config["completion_status"] == "failed" and "增益" in config["failure_reason"]
    assert (directory / "raw" / "gain_check.npz").is_file()
    assert not any(e[0] == "sg_sine" for e in value.events)
    assert_shutdown_order(value.events)


def test_gain_below_declared_aborts_with_shutdown(tmp_path, monkeypatch):
    """增益低于声明（例如参数写了 20 dB、功放实际只有 0 dB）时按 GAIN_LOWER_RATIO 中止。"""
    copy_config(tmp_path)
    value = Rig(true_gain=1.0)
    install(monkeypatch, value)
    with pytest.raises(SafetyAbort, match="低于同一预测值"):
        workflow.run(small_params(), project_root=tmp_path)
    directory = next((tmp_path / "data" / "coil_drive_characterization").iterdir())
    assert read_config(directory)["completion_status"] == "failed"
    assert not any(e[0] == "sg_sine" for e in value.events)
    assert_shutdown_order(value.events)


def test_amplifier_settings_written_before_any_output(tmp_path, rig):
    """连接后先按参数写 1200B 增益/输入/输出阻抗，并把信号源负载配成与输入阻抗一致；写入在开输出之前。"""
    directory = workflow.run(small_params(), project_root=tmp_path)
    events = rig.events
    first_output = min(index for index, event in enumerate(events) if event == ("amp_output", True))
    for expected in (("amp_input_ohm", 1_000_000.0), ("amp_output_ohm", 50.0), ("amp_gain", 20.0)):
        assert expected in events[:first_output], f"{expected} 应在开启功放输出之前写入"
    assert ("sg_load", "INF") in events[:first_output]
    assert read_config(directory)["completion_status"] == "completed"
    assert rig.amp_settings == {"gain_db": 20.0, "input_impedance_ohm": 1_000_000.0,
                                "output_impedance_ohm": 50.0}


def test_amplifier_settings_not_written_when_disabled(tmp_path, rig):
    """AMPLIFIER_WRITE_SETTINGS=false 时只按声明值预测，不向 1200B 发增益/阻抗命令。"""
    workflow.run(small_params(amplifier_write_settings=False), project_root=tmp_path)
    assert not any(event[0] in ("amp_gain", "amp_input_ohm", "amp_output_ohm") for event in rig.events)


def test_amplifier_settings_rejected_while_output_on(monkeypatch, tmp_path):
    """功放输出开启时安全回调拒绝改增益或阻抗，命令不会发出。"""
    from types import SimpleNamespace

    from .preparation import amplifier_safety_check

    ctx = SimpleNamespace(params=small_params(), root=tmp_path, state={"amp_output_on": True})
    check = amplifier_safety_check(ctx)
    check("output", False)  # 关闭输出始终放行
    with pytest.raises(ValueError, match="输出开启时不允许修改"):
        check("gain_db", 10.0)
    ctx.state["amp_output_on"] = False
    with pytest.raises(ValueError, match="不修改功放"):
        check("save_settings", True)


def test_measured_overcurrent_aborts(tmp_path, monkeypatch):
    copy_config(tmp_path)
    value = Rig(offset_a=0.31)
    install(monkeypatch, value)
    with pytest.raises(SafetyAbort, match="越过限值"):
        workflow.run(small_params(), project_root=tmp_path)
    assert_shutdown_order(value.events)


def test_missing_sync_aborts_sweep_with_shutdown(tmp_path, monkeypatch):
    """同步缺失（DG4000 处于 DC 波形或同步未输出）时，NORMal 触发的扫频帧即中止并安全收尾。"""
    copy_config(tmp_path)
    value = Rig(sync_peak_v=0.1)  # 同步通道只有噪声幅度，远低于 1.0 V 阈值
    install(monkeypatch, value)
    with pytest.raises(SafetyAbort, match="同步信号缺失"):
        workflow.run(small_params(), project_root=tmp_path)
    # A03 用 AUTO 强制触发不依赖同步，应已完成；扫频首帧即中止。
    assert any(e[0] == "sg_sine" for e in value.events)  # 已进入扫频并设了首个正弦
    assert_shutdown_order(value.events)


def test_clamped_pulse_width_aborts_before_capture(tmp_path, monkeypatch):
    """信号源把脉宽静默钳位时立即中止，不把与设定不符的波形当有效脉冲。"""
    copy_config(tmp_path)
    value = Rig()
    value.pulse_width_clamp_s = 62.5e-6   # 例如 DG 在 50 Hz 下的最小脉宽
    install(monkeypatch, value)
    with pytest.raises(SafetyAbort, match="未生效"):
        workflow.run(small_params(), project_root=tmp_path)
    directory = next((tmp_path / "data" / "coil_drive_characterization").iterdir())
    assert not list((directory / "raw").glob("pulse_*.npz"))
    assert_shutdown_order(value.events)


def test_valid_sync_passes_sweep(tmp_path, rig):
    """正常 3.3 V 同步下扫频不被同步预检拦截（回归对照）。"""
    directory = workflow.run(small_params(), project_root=tmp_path)
    assert read_config(directory)["completion_status"] == "completed"
    assert len(list((directory / "raw").glob("sine_*.npz"))) == 7


def test_sync_armed_once_per_stage_after_first_waveform_setup(tmp_path, rig):
    """每个扫描段只在首帧锁存一次同步（关输出→开同步→开输出）；换频 APPLy 不重新锁存。"""
    workflow.run(small_params(), project_root=tmp_path)
    events = rig.events
    arming = [("sg_output", False), ("sg_sync", True), ("sg_output", True)]
    positions = [index for index in range(len(events)) if events[index:index + 3] == arming]
    assert len(positions) == 2, f"应在 A04/A05 各锁存一次：{positions}"
    for start in positions:
        assert events[start - 1][0] in ("sg_sine", "sg_pulse")


def test_transient_sync_loss_is_rearmed_and_reframed(tmp_path, rig):
    """同步锁存偶发失败（真机约三成）时丢帧重采，不把整段扫频判成失败。"""
    rig.drop_sync_frames = 1
    directory = workflow.run(small_params(), project_root=tmp_path)
    assert read_config(directory)["completion_status"] == "completed"
    assert len(list((directory / "raw").glob("sine_*.npz"))) == 7


def test_cancel_mid_sweep_shuts_down_and_keeps_frames(tmp_path, rig):
    rig.cancel_at = 8  # 5 帧零偏 + 1 帧增益 + 第 2 个扫频点
    ctx = workflow.Workflow(small_params(), project_root=tmp_path)
    with pytest.raises(WorkflowCancelled):
        ctx.execute(["A01", "A02", "A03", "A04"])
    ctx.finish(WorkflowCancelled("模拟取消"))
    assert ctx.states["A04"]["status"] == "cancelled" and ctx.states["A06"]["status"] == "completed"
    assert (ctx.directory.root / "raw" / "sine_00.npz").is_file()
    assert read_config(ctx.directory.root)["completion_status"] == "cancelled"
    assert_shutdown_order(rig.events)


def test_missing_current_limit_refuses_before_connection(tmp_path, monkeypatch):
    copy_config(tmp_path, drop_current_limit=True)
    value = Rig()
    install(monkeypatch, value)
    with pytest.raises(ValueError, match="z_pulse_coil_current"):
        workflow.Workflow(small_params(), project_root=tmp_path)
    assert value.events == []


@pytest.mark.parametrize("changes, message", [
    ({"sine_peak_current_ma": 350.0}, "安全拦截"),
    ({"pulse_repetition_hz": 6000.0}, "占空比"),
    ({"scope_sync_channel": 2}, "不能相同"),
    ({"frequency_start_hz": 2e6}, "低于终止频率"),
    ({"sine_peak_current_ma": 300.0, "resistor_rated_w": 0.5}, "功耗"),
])
def test_preflight_rejects_unsafe_or_inconsistent_plans(tmp_path, changes, message):
    copy_config(tmp_path)
    with pytest.raises(ValueError, match=message):
        preparation.preflight(small_params(**changes), tmp_path)


def test_stepwise_rerun_copies_zero_frames_to_new_directory(tmp_path, rig):
    ctx = workflow.Workflow(small_params(), project_root=tmp_path)
    ctx.execute(["A01", "A02", "A03", "A04"])
    first = ctx.directory.root
    assert read_config(first)["completion_status"] == "completed"
    ctx.update_parameters({"FREQUENCY_STOP_HZ": 1e5})
    assert ctx.states["A03"]["status"] == "completed" and ctx.states["A04"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        stages.validate_selection(stages.ACQUISITION, {**ctx.states, "A03": {"status": "pending", "error": ""}}, ["A04"])
    ctx.execute(["A04"])
    second = ctx.directory.root
    assert second != first and read_config(second)["zero_inherited_from"] == first.name
    assert (second / "raw" / "zero_on_00.npz").is_file() and len(list(second.glob("raw/sine_*.npz"))) == 5
    assert len(list(first.glob("raw/sine_*.npz"))) == 7  # 旧轮次原样保留
    ctx.finish()
    result = analysis.analyze(second, step_ids=["B01"])
    assert result["status"] == "partial" and len(result["transfer"]) == 5
    assert_shutdown_order(rig.events)


def test_every_visible_parameter_has_an_edit_entry():
    visible = set(CoilDriveCharacterizationParams().to_external())
    flow = stages.definition()
    shown = {key for stage in flow["acquisition"] + flow["analysis"] for key in stage["display_parameters"]}
    assert visible == shown
    assert flow["parameter_impacts"]["acquisition"]["AMPLIFIER_GAIN_DB"] == ["A03", "A04", "A05"]
    assert flow["parameter_impacts"]["acquisition"]["SCOPE_ACQUIRE_DELAY_S"] == ["A03", "A04", "A05"]
    assert flow["parameter_impacts"]["acquisition"]["PULSE_WIDTHS_US"] == ["A05"]
    assert flow["parameter_impacts"]["analysis"]["AREA_TOLERANCE"] == ["B02"]


def test_default_yaml_round_trip():
    path = ROOT / "params" / "experiments" / "coil-drive-characterization.yaml"
    params = CoilDriveCharacterizationParams.from_yaml(path)
    assert isinstance(params, ExperimentParams) and params.to_external()["PULSE_WIDTHS_US"]
    assert CoilDriveCharacterizationParams.from_external(params.to_external()) == params


COIL_TAU_S = 0.7e-6  # 2026-11-14 实测：−3 dB 230 kHz、10–90% 上升 1.43 µs → τ≈0.67–0.69 µs


def test_pulse_window_covers_tail_and_stays_readable():
    """默认参数下每个脉宽的后半窗都装得下「脉宽 + 5τ」，且脉宽不超过窗长的 40%。"""
    params = CoilDriveCharacterizationParams()
    for width in params.pulse_widths_s:
        window = preparation.pulse_window_s(params, width)
        assert window / 2 >= width + 5 * COIL_TAU_S, f"{width * 1e6:g} µs 的后半窗装不下拖尾"
        assert window / 2 >= 2.5 * width, f"{width * 1e6:g} µs 触发后可用窗口不足 2.5 倍脉宽"
        assert width / window <= 0.4


def test_sine_window_is_closed_form_timebase_with_bounded_cycles():
    """扫频时基取 1-2-5 序列，一屏周期数落在 [SINE_CYCLES, 2.5×SINE_CYCLES)，无需搜索循环。"""
    params = CoilDriveCharacterizationParams()
    for frequency in params.frequencies():
        duration = preparation.nice_up(params.sine_cycles / frequency / 10) * 10
        cycles = duration * frequency
        assert params.sine_cycles - 1e-9 <= cycles < 2.5 * params.sine_cycles
        scale = duration / 10
        mantissa = scale / 10 ** math.floor(math.log10(scale))
        assert min(abs(mantissa - value) for value in (1.0, 2.0, 5.0)) < 1e-9


def fake_main():
    """GUI 后端测试的子进程入口：真实会话协议 + 假仪器。"""
    import os
    import sys
    root = Path(os.environ["COIL_DRIVE_TEST_ROOT"])
    payload = json.loads(os.environ["LAB_TYPED_PARAMETERS"])
    session_id = payload.pop("session_id", None)
    params = CoilDriveCharacterizationParams.from_external(payload)
    with pytest.MonkeyPatch.context() as mp:
        install(mp, Rig())
        if os.environ.get("COIL_DRIVE_TEST_FULL") == "1":
            workflow.run(params, project_root=root)
        else:
            from lab_workflows.staged_workflow import serve_workflow
            serve_workflow(workflow.Workflow(params, project_root=root, session_id=session_id), sys.stdin)


if __name__ == "__main__":
    fake_main()


def test_contract_preflight_accepts_amplifier_and_scope_mapping(monkeypatch):
    """通用映射预检按功放/示波器类型校验新键，不按信号源要求 channel。"""
    from ... import common, instrument_config
    from ...experiments.contracts import ExperimentDefinition

    mapping = {
        "Z_magnetic_field": {"instrument": "signal_generator", "channel": 1},
        "coil_drive_amplifier": {"instrument": "power_amplifier", "device_id": "amp"},
        "coil_current_scope": {"instrument": "oscilloscope", "device_id": "scope"},
    }
    monkeypatch.setattr(common, "load_mapping", lambda: mapping)
    monkeypatch.setattr(instrument_config, "validate_mapping_usage", lambda keys: None)
    definition = ExperimentDefinition(
        id="coil-drive-characterization", title="", category="", family="",
        variant="", description="", data_type="", required_devices=(),
    )
    assert definition._mapping_errors() == []
