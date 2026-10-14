"""已知噪声生成、上传与实际设置回读。"""
import math
from signal_generator.types import normalize_output_load
from ...common import validate_safety_limit
from ...steps import ArbitraryWaveformSpec, upload_arbitrary
from ...experiment_runtime import check_cancelled
from .generation import generate_known_noise_waveform, load_z_calibration


def validate_noise_levels(amplitude_vpp, limits):
    for voltage in (-amplitude_vpp / 2, amplitude_vpp / 2):
        validate_safety_limit("Z_magnetic_field", voltage, limits)


def upload_noise_waveform(generator, waveform, params, limits):
    validate_noise_levels(params.noise_amplitude_vpp, limits)
    check_cancelled()
    upload_arbitrary(generator, ArbitraryWaveformSpec(values=waveform.normalized,
        frequency=params.noise_repeat_freq_hz, amplitude=params.noise_amplitude_vpp,
        offset=0.0, phase=0.0, channel=1, output=False))
    generator.set_frequency(params.noise_repeat_freq_hz, channel=1)
    generator.set_amplitude(params.noise_amplitude_vpp, channel=1)
    generator.set_offset(0.0, channel=1)
    generator.wait_for_operation_complete()
    return verify_noise_waveform(generator, waveform, params, limits)


def verify_noise_waveform(generator, waveform, params, limits):
    """扫描前只回读并拒绝漂移，避免重写任意波和改变相位。"""
    check_cancelled()
    actual = dict(shape=str(generator.get_shape(channel=1)).strip().upper(),
        points=int(generator.get_arb_points(channel=1)),
        amplitude_vpp=float(generator.get_amplitude(channel=1)),
        offset_v=float(generator.get_offset(channel=1)), frequency_hz=float(generator.get_frequency(channel=1)),
        voltage_unit=str(generator.get_voltage_unit(channel=1)).strip().upper(),
        load=normalize_output_load(generator.get_output_load(channel=1)))
    for voltage in (actual["offset_v"] - actual["amplitude_vpp"] / 2,
                    actual["offset_v"] + actual["amplitude_vpp"] / 2):
        validate_safety_limit("Z_magnetic_field", voltage, limits)
    expected = dict(points=len(waveform.normalized), amplitude_vpp=params.noise_amplitude_vpp,
                    offset_v=0.0, frequency_hz=params.noise_repeat_freq_hz, voltage_unit="VPP", load="INF")
    problems = [f"{key}: 请求 {value}，回读 {actual[key]}" for key, value in expected.items()
                if (actual[key] != value if isinstance(value, (str, int)) else
                    not math.isclose(actual[key], value, rel_tol=1e-5, abs_tol=1e-6))]
    if actual["shape"] not in {"USER", "CUSTOM"}:
        problems.append("Z 任意波形状回读不一致")
    generator.raise_for_errors()
    if problems:
        raise RuntimeError("噪声任意波设置回读不一致：" + "；".join(problems))
    return actual


def prepare_noise(ctx):
    params = ctx.params
    waveform = generate_known_noise_waveform(repeat_freq_hz=params.noise_repeat_freq_hz,
                                              bands=params.noise_bands(), seed=params.noise_seed)
    calibration = load_z_calibration(ctx.root, params.z_calibration_source_run)
    actual = upload_noise_waveform(ctx.devices["dg_sweep"], waveform, params, ctx.limits)
    ctx.noise_waveform, ctx.z_calibration, ctx.noise_readback = waveform, calibration, actual
    ctx.outputs["Z"] = "OFF"
