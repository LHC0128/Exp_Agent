"""复用 XY 准备步骤，增加已知噪声与真值链预检。"""
from ...common import load_mapping, load_safety_limits
from ...instrument_config import validate_mapping_usage
from ...lockin_compatibility import require_lockin_compatibility
from ...experiments.requirements import NOISE_SPECTRUM_XY
from ..noise_spectrum_xy.preparation import build_target_scan_axes
from ..noise_spectrum_xy.phase import validate_sine_peak
from ..noise_spectrum_xy.excitation import validate_pump_levels, validate_trigger_square_levels
from .models import NoiseSpectrumXYKnownNoiseParams
from .excitation import validate_noise_levels
from ..noise_spectrum_xy.preparation import connect_instruments as connect_xy_instruments
from .safety import safe_connection_outputs_off


def preflight(params, root):
    params = NoiseSpectrumXYKnownNoiseParams.from_external(params.to_external())
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    validate_mapping_usage(NOISE_SPECTRUM_XY, root)
    mapping, limits = load_mapping(root), load_safety_limits(root)
    require_lockin_compatibility("noise-spectrum-xy-known-noise", params.to_external(), mapping=mapping)
    axes = build_target_scan_axes(params, limits)
    validate_sine_peak(params.xy_calib_envelope_v, limits)
    validate_trigger_square_levels(params, limits)
    validate_pump_levels(params, limits)
    validate_noise_levels(params.noise_amplitude_vpp, limits)
    return params, mapping, limits, axes


def connect_instruments(ctx):
    def shutdown_partial(session):
        report = safe_connection_outputs_off(session, ctx.mapping)
        ctx.shutdown = report.to_dict()
        if report.errors:
            print("连接安全收尾失败：" + "；".join(report.errors), flush=True)
    return connect_xy_instruments(ctx.mapping, on_failure=shutdown_partial)
