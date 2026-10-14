"""Probe AOM 载波、AM 噪声生成上传与实际设置回读。"""
import math
from signal_generator.types import normalize_output_load
from ...common import validate_safety_limit
from ...steps import ArbitraryWaveformSpec, upload_arbitrary
from ...experiment_runtime import check_cancelled
from ..noise_spectrum_xy_known_noise.generation import generate_known_noise_waveform


def generate_probe_am_waveforms(params):
    """纯计算生成 AM 噪声与可选 Z 可控噪声波形；不访问硬件。"""
    noise = generate_known_noise_waveform(repeat_freq_hz=params.noise_repeat_freq_hz,
                                           bands=params.noise_bands(), seed=params.noise_seed)
    controlled = generate_known_noise_waveform(repeat_freq_hz=params.z_noise_repeat_freq_hz,
                                                bands=params.z_noise_bands(), seed=params.z_noise_seed) \
        if params.inject_controlled_noise else None
    return noise, controlled


def configure_probe_carrier(ctx):
    """配置 Probe AOM 载波正弦与外部 AM；载波 ON、噪声 OFF 待命。"""
    params, limits, mapping = ctx.params, ctx.limits, ctx.mapping
    generator = ctx.devices["dg_probe_am"]
    carrier_channel = int(mapping["Probe_AOM_Carrier"]["channel"])
    noise_channel = int(mapping["Probe_AOM_AM"]["channel"])
    validate_safety_limit("Probe_AOM_Carrier", params.carrier_amplitude_vpp, limits)
    generator.set_output(False, channel=carrier_channel)
    generator.setup_sine(freq=params.carrier_frequency_hz, amplitude=params.carrier_amplitude_vpp,
                         offset=0.0, phase=0.0, channel=carrier_channel)
    generator.set_mod_type("AM", channel=carrier_channel)
    generator.set_mod_am_source("EXT", channel=carrier_channel)
    generator.set_mod_am_depth(params.am_depth_percent, channel=carrier_channel)
    generator.set_mod_state(True, channel=carrier_channel)
    generator.set_voltage_unit("VPP", channel=noise_channel)
    generator.set_output(False, channel=noise_channel)
    # 载波是校相与采集的信号来源，配置完成即开启并保持到安全收尾。
    generator.set_output(True, channel=carrier_channel)
    ctx.outputs["Probe AM"] = "载波 ON·噪声 OFF"


def _verify_noise_waveform(generator, channel, safety_key, waveform, *, repeat_freq_hz, amplitude_vpp, limits=None):
    """扫描前只回读并拒绝漂移，避免重写任意波和改变相位。"""
    check_cancelled()
    actual = dict(shape=str(generator.get_shape(channel=channel)).strip().upper(),
        points=int(generator.get_arb_points(channel=channel)),
        amplitude_vpp=float(generator.get_amplitude(channel=channel)),
        offset_v=float(generator.get_offset(channel=channel)), frequency_hz=float(generator.get_frequency(channel=channel)),
        voltage_unit=str(generator.get_voltage_unit(channel=channel)).strip().upper(),
        load=normalize_output_load(generator.get_output_load(channel=channel)))
    for voltage in (actual["offset_v"] - actual["amplitude_vpp"] / 2,
                    actual["offset_v"] + actual["amplitude_vpp"] / 2):
        validate_safety_limit(safety_key, voltage, limits)
    expected = dict(points=len(waveform.normalized), amplitude_vpp=amplitude_vpp,
        offset_v=0.0, frequency_hz=repeat_freq_hz, voltage_unit="VPP", load="INF")
    problems = [f"{key}: 请求 {value}，回读 {actual[key]}" for key, value in expected.items()
        if (actual[key] != value if isinstance(value, (str, int)) else
            not math.isclose(actual[key], value, rel_tol=1e-5, abs_tol=1e-6))]
    if actual["shape"] not in {"USER", "CUSTOM"}:
        problems.append("噪声任意波形状回读不一致")
    generator.raise_for_errors()
    if problems:
        raise RuntimeError("噪声任意波设置回读不一致：" + "；".join(problems))
    return actual


def upload_noise_waveform(generator, channel, safety_key, waveform, *, repeat_freq_hz, amplitude_vpp, limits=None):
    """上传噪声波形并显式重写参数后回读；输出保持 OFF 待命。"""
    for voltage in (-amplitude_vpp / 2, amplitude_vpp / 2):
        validate_safety_limit(safety_key, voltage, limits)
    check_cancelled()
    upload_arbitrary(generator, ArbitraryWaveformSpec(values=waveform.normalized,
        frequency=repeat_freq_hz, amplitude=amplitude_vpp, offset=0.0, phase=0.0,
        channel=channel, output=False))
    # [经验] DG4162 上传后必须显式重写参数并回读；DC 恢复路径由驱动选择 CUSTom。
    generator.set_frequency(repeat_freq_hz, channel=channel)
    generator.set_amplitude(amplitude_vpp, channel=channel)
    generator.set_offset(0.0, channel=channel)
    generator.wait_for_operation_complete()
    return _verify_noise_waveform(generator, channel, safety_key, waveform,
                                  repeat_freq_hz=repeat_freq_hz, amplitude_vpp=amplitude_vpp, limits=limits)


def verify_am_noise(generator, channel, waveform, params, limits=None):
    return _verify_noise_waveform(generator, channel, "Probe_AOM_AM", waveform,
        repeat_freq_hz=params.noise_repeat_freq_hz, amplitude_vpp=params.noise_amplitude_vpp, limits=limits)


def verify_z_noise(generator, channel, waveform, params, limits=None):
    return _verify_noise_waveform(generator, channel, "Z_magnetic_field", waveform,
        repeat_freq_hz=params.z_noise_repeat_freq_hz, amplitude_vpp=params.z_noise_amplitude_vpp, limits=limits)


def prepare_noise(ctx):
    """生成并上传 AM 噪声任意波；按参数同时准备 Z 可控噪声，输出保持 OFF。"""
    params, mapping = ctx.params, ctx.mapping
    noise, controlled = generate_probe_am_waveforms(params)
    ctx.noise_waveform, ctx.z_waveform = noise, controlled
    ctx.noise_readback = upload_noise_waveform(ctx.devices["dg_probe_am"],
        int(mapping["Probe_AOM_AM"]["channel"]), "Probe_AOM_AM", noise,
        repeat_freq_hz=params.noise_repeat_freq_hz, amplitude_vpp=params.noise_amplitude_vpp,
        limits=ctx.limits)
    if controlled is not None:
        ctx.z_readback = upload_noise_waveform(ctx.devices["dg_sweep"],
            int(mapping["Z_magnetic_field"]["channel"]), "Z_magnetic_field", controlled,
            repeat_freq_hz=params.z_noise_repeat_freq_hz, amplitude_vpp=params.z_noise_amplitude_vpp,
            limits=ctx.limits)
    else:
        ctx.z_readback = None
    ctx.outputs.update(**{"Probe AM": "载波 ON·噪声 OFF"}, Z="OFF")
