"""同一 Z 端口上传物理求和后的任意波。"""
import numpy as np
import math
from ...common import validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import ArbitraryWaveformSpec, upload_arbitrary
from ..rf_sensitivity_direct_aw._helpers import wait
from .waveform import synthesize_z


def upload_z(ctx, amplitude, phase_base=None):
    check_cancelled()
    phase = ctx.params.z_burst_phase_init if phase_base is None else float(phase_base)
    wave = synthesize_z(ctx.noise, amplitude, ctx.z_rf_frequency_hz, phase)
    device, channel = ctx.devices["signals"], ctx.devices["channels"]["z"]
    device.set_output(False, channel=channel)
    upload_arbitrary(device, ArbitraryWaveformSpec(wave["normalized"], wave["repeat_frequency_Hz"],
        wave["vpp_V"], 0.0, phase=0.0, channel=channel))
    actual = {"vpp_V": float(device.get_amplitude(channel=channel)),
        "offset_V": float(device.get_offset(channel=channel)),
        "repeat_frequency_Hz": float(device.get_frequency(channel=channel))}
    if any(not math.isclose(actual[key], wave[key], rel_tol=1e-5, abs_tol=1e-6) for key in actual):
        raise RuntimeError("Z 叠加波形幅度、偏置或重复频率回读不一致，禁止采集")
    for level in (actual["offset_V"] - actual["vpp_V"] / 2, actual["offset_V"] + actual["vpp_V"] / 2):
        validate_safety_limit("Z_magnetic_field", level)
    wave.update({"actual_" + key: value for key, value in actual.items()})
    device.set_burst_mode("INFinity", channel=channel)
    device.set_burst_trigger_source("EXTernal", channel=channel)
    device.set_burst_trigger_slope("POSitive", channel=channel)
    # 总波形的触发相位恒为零，RF 相位只进入正弦项，避免平移固定噪声。
    device.set_burst_phase(0.0, channel=channel)
    device.set_burst_state(True, channel=channel)
    device.set_output(True, channel=channel)
    ctx.outputs["Z"] = "ON"
    stage = ctx.current_step or "prepare"
    index = ctx.state.setdefault("z_waveform_records", {}).get(stage, 0)
    name = f"z_sum_{stage}_{index:05d}.npz"
    pending = getattr(ctx, "pending_writes", None)
    if pending is None:
        np.savez(ctx.directory.raw / name, **wave)
    else:
        pending.add(np.savez, ctx.directory.raw / name, **wave)
    ctx.state["z_waveform_records"][stage] = index + 1
    ctx.state["data_files"].append("raw/" + name)
    ctx.state["z_composite_output"] = {key: wave[key] for key in ("vpp_V", "offset_V", "rf_vpp_V", "rf_frequency_Hz", "rf_phase_deg", "repeat_frequency_Hz")}
    ctx.state["z_composite_output"].update({"actual_" + key: value for key, value in actual.items()})
    if pending is None:
        ctx.save()
    else:
        pending.add(ctx.save)
    wait(1.5 / ctx.params.xy_trigger_freq)
    return (phase + (180 if amplitude < 0 else 0)) % 360
