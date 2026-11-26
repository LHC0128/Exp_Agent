"""把 Z 脉冲控制场打开并保持（供示波器观察）；--off 关闭并恢复。

打开时：沿用 rf-phase-sensitivity 的准备步骤（时钟、温控、1200B、GS200 脉冲工作点、5 kHz 共同触发
与 Z 通道突发脉冲），随后关闭 MFLI 射频，开 Z 信号源与 1200B 输出并回读确认；脚本退出后**保持脉冲
输出**，供在示波器上观察 CH3 触发与线圈电流/光学信号。

关闭时（--off）：关 1200B、关闭 Z 信号源输出、恢复 X/Y 偏置、执行安全收尾并回读。

用法（在运行目录经 GUI 代跑，脚本内不调用任何客户端接口）：
    scripts/_devices/z_pulse_on_hold.py            # 打开并保持脉冲
    scripts/_devices/z_pulse_on_hold.py --off      # 观察结束，关闭并收尾
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import yaml

try:
    import hardware_gate as _hg  # noqa: F401
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

from hardware_gate import require_hardware_access, runtime_root

from lab_workflows.common import data_root, load_safety_limits
from lab_workflows.experiment_modules.rf_phase_sensitivity import preparation, run_format
from lab_workflows.experiment_modules.rf_phase_sensitivity.models import RfPhaseSensitivityParams
from lab_workflows.experiment_modules.z_chain_common import prep as z_prep
from lab_workflows.steps import DeviceSession, DisconnectTarget, run_safety_shutdown
from lab_workflows.steps.coil_drive_chain import SafetyAbort


class _Ctx:
    """与 rf_phase_sensitivity 准备步骤兼容的轻量上下文；不写研究目录。"""

    def __init__(self, params: RfPhaseSensitivityParams, root: Path) -> None:
        self.root = Path(root).resolve()
        self.params, self.mapping = preparation.preflight(params, self.root)
        self.device_session = DeviceSession()
        self.devices: dict[str, Any] = {}
        self.outputs: dict[str, str] = {}
        self.active = False
        self.main_field_state = None
        self.state = run_format.initial_metadata(self.params)
        self.directory = None

    def save(self) -> None:
        return None


def _load_params(root: Path) -> RfPhaseSensitivityParams:
    defaults_path = root / "params" / "experiments" / f"{run_format.EXPERIMENT_ID}.yaml"
    base = RfPhaseSensitivityParams.from_yaml(defaults_path)
    merged = base.to_external()
    schema_ver = RfPhaseSensitivityParams.schema_version
    local_path = defaults_path.with_name(f"{run_format.EXPERIMENT_ID}.local.yaml")
    if local_path.is_file():
        loaded = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            overlay = loaded.get("parameters", loaded)
            if isinstance(overlay, dict):
                merged.update(overlay)
            if isinstance(loaded.get("schema_version"), int):
                schema_ver = int(loaded["schema_version"])
    params = RfPhaseSensitivityParams.from_external(merged, schema_version=schema_ver)
    errors = params.validate(root)
    if errors:
        raise ValueError("参数校验失败：" + "；".join(errors))
    return params


def _disable_rf(ctx: _Ctx) -> None:
    lockin = ctx.devices["hf2"]
    dev = lockin.device_id
    if int(lockin.get_int(f"/{dev}/sigouts/0/on")) != 0:
        lockin.set_int(f"/{dev}/sigouts/0/on", 0)
        lockin.sync()
    if int(lockin.get_int(f"/{dev}/sigouts/0/on")) != 0:
        raise SafetyAbort("MFLI Out1 未回读为 OFF")


def _fourier_pulse_wave(params: RfPhaseSensitivityParams, harmonics: int,
                        points: int = 16384) -> tuple[Any, dict[str, Any]]:
    """与现脉冲同结构的 ±π 双脉冲（升余弦、无拖尾补偿）取 5 kHz 基频的 1..H 次谐波重构。

    基准波形：周期起点 +π 脉冲、半周期处 −π 脉冲，脉宽与沿宽取自实验参数；对其做 DFT、只保留
    |k| ≤ H 的谐波再逆变换，得到去掉拖尾的傅里叶截断版本；再按 +π 半周期面积归一，使翻转角
    与原脉冲一致（截断本身的面积损失补回，峰值随之升高但仍低于方脉冲）。
    """
    import numpy as np
    from lab_workflows.experiment_modules.z_chain_stability import processing as pulse_processing

    period_us = 1e6 / float(params.y_rf_freq_hz)
    width_us = float(params.z_pulse_width_us)
    edge_us = float(params.z_pulse_edge_us)
    base = pulse_processing.pulse_waveform(period_us, width_us, edge_us, points=points)
    spectrum = np.fft.rfft(base)
    kept = np.zeros_like(spectrum)
    kept[0] = spectrum[0]
    kept[1:harmonics + 1] = spectrum[1:harmonics + 1]
    wave = np.fft.irfft(kept, n=points)
    half = points // 2
    # ±π 双脉冲整周期净面积为 0，按 +π 半周期的面积比衡量翻转角；截断会损失面积，这里补回来。
    raw_area_ratio = float(np.sum(wave[:half]) / np.sum(base[:half]))
    if raw_area_ratio <= 0:
        raise ValueError("傅里叶截断后 +π 半周期面积为非正")
    wave = wave / raw_area_ratio
    peak = float(np.max(np.abs(wave)))
    if peak > 1.0:
        raise ValueError(f"傅里叶截断并补面积后峰值 {peak:.3f} 超出归一化范围，需降低脉宽或谐波阶数")
    record = dict(harmonics=harmonics, points=points, width_us=width_us, edge_us=edge_us,
                  base_peak=float(np.max(np.abs(base))), peak_normalized=peak,
                  vpp=float(params.pulse_vpp), peak_voltage_v=peak * float(params.pulse_vpp),
                  raw_area_ratio=raw_area_ratio, area_ratio=1.0,
                  antisymmetry_error=float(np.max(np.abs(wave[:half] + wave[half:]))))
    return wave, record


def _pulse_state(ctx: _Ctx) -> dict[str, Any]:
    z_dev, z_ch = preparation._route(ctx, "z")  # noqa: SLF001
    return dict(
        z_dg_output=preparation.dg_output(z_dev, z_ch),
        z_dg_frequency_hz=float(z_dev.get_frequency(channel=z_ch)),
        z_dg_vpp=float(z_dev.get_amplitude(channel=z_ch)),
        z_dg_burst=bool(z_dev.get_burst_state(channel=z_ch)),
        amp_output_on=bool(ctx.state.get("amp_output_on")),
        temp_switch=preparation.write_temperature(ctx, True),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="打开/关闭并保持 Z 脉冲控制场，供示波器观察")
    parser.add_argument("--off", action="store_true", help="关闭脉冲并安全收尾")
    parser.add_argument("--fourier-harmonics", type=int, default=0, metavar="H",
                        help="用单个脉冲的傅里叶截断（直流到第 H 次谐波）替代原突发波形；0 表示保持原波形")
    args = parser.parse_args(argv)

    require_hardware_access("Z 脉冲观察保持")
    root = runtime_root()
    params = _load_params(root)
    load_safety_limits(root)  # 触发限值文件校验；具体电平由 preflight 逐项校验
    ctx = _Ctx(params, root)
    errors: list[str] = []
    keep_outputs = False
    try:
        preparation.connect_and_synchronize(ctx)
        if not args.off:
            preparation.prepare_measurement(ctx)
            _disable_rf(ctx)
            z_dev, z_ch = preparation._route(ctx, "z")  # noqa: SLF001
            if args.fourier_harmonics > 0:
                wave, wave_record = _fourier_pulse_wave(params, int(args.fourier_harmonics))
                z_prep.set_amp_output(ctx, False)
                z_dev.set_output(False, channel=z_ch)
                z_dev.set_burst_state(False, channel=z_ch)
                if preparation.dg_output(z_dev, z_ch):
                    raise SafetyAbort("Z 信号源未回读为 OFF，拒绝上传傅里叶波形")
                z_dev.setup_arbitrary(wave, float(params.y_rf_freq_hz), float(params.pulse_vpp), 0.0,
                                      output=False, channel=z_ch)
                z_dev.set_burst_mode("INFinity", channel=z_ch)
                z_dev.set_burst_ncycles(z_prep.BURST_NCYCLES, channel=z_ch)
                z_dev.set_burst_trigger_source("EXTernal", channel=z_ch)
                z_dev.set_burst_trigger_slope("POSitive", channel=z_ch)
                z_dev.set_burst_phase(0.0, channel=z_ch)
                z_dev.set_burst_state(True, channel=z_ch)
                z_dev.wait_for_operation_complete()
                z_dev.raise_for_errors()
                got = dict(shape=z_dev.get_shape(channel=z_ch),
                           points=int(z_dev.get_arb_points(channel=z_ch)),
                           vpp=float(z_dev.get_amplitude(channel=z_ch)),
                           freq=float(z_dev.get_frequency(channel=z_ch)),
                           burst=bool(z_dev.get_burst_state(channel=z_ch)))
                if got["points"] != int(wave_record["points"]) or got["shape"].upper() not in {"USER", "ARB"}:
                    raise SafetyAbort(f"傅里叶波形上传回读不符：{got}")
                print("[傅里叶脉冲] " + "，".join(f"{k}={v}" for k, v in {**wave_record, **got}.items()), flush=True)
            if not preparation.dg_output(z_dev, z_ch):
                z_dev.set_output(True, channel=z_ch)
                z_dev.wait_for_operation_complete()
                if not preparation.dg_output(z_dev, z_ch):
                    raise SafetyAbort("Z 信号源输出未回读为 ON")
            z_prep.set_amp_output(ctx, True)
            state = _pulse_state(ctx)
            if not (state["z_dg_output"] and state["amp_output_on"]):
                raise SafetyAbort(f"脉冲链未进入预期状态：{state}")
            print("[脉冲已开] " + "，".join(f"{k}={v}" for k, v in state.items()), flush=True)
            print("脚本退出后保持脉冲输出；观察结束请用 --off 关闭。", flush=True)
            keep_outputs = True
            return 0
        # --off：关闭脉冲链并安全收尾
        z_prep.set_amp_output(ctx, False)
        z_dev, z_ch = preparation._route(ctx, "z")  # noqa: SLF001
        if preparation.dg_output(z_dev, z_ch):
            z_dev.set_output(False, channel=z_ch)
            z_dev.wait_for_operation_complete()
        if preparation.dg_output(z_dev, z_ch):
            raise SafetyAbort("Z 信号源输出未回读为 OFF")
        print("[脉冲已关] " + str(_pulse_state(ctx)), flush=True)
    finally:
        if ctx.active and not keep_outputs:
            errors = preparation.park(ctx)
        devices = reversed(ctx.device_session.unique_devices())
        run_safety_shutdown(disconnect_targets=(DisconnectTarget(str(i), d) for i, d in enumerate(devices)))
        if errors:
            raise SafetyAbort("收尾存在未完成项：" + "；".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
