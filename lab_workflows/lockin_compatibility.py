"""锁相放大器的离线能力预检；不连接硬件，不替换接线。"""

import re
import numpy as np
from pathlib import Path
from typing import Any

from lockin_amplifier import lockin_capabilities
from .common import load_mapping


DUAL_VOLTAGE_EXPERIMENTS = frozenset({
    "rf-sensitivity-aw", "rf-sensitivity-aw-r", "rf-sensitivity-direct-aw-frequency",
    "noise-spectrum-xy-demod3-r", "xy-direct-aw-dc-calibration",
    "rf-sensitivity-aw-frequency", "rf-sensitivity-constxy-frequency",
    "xy-aw-voltage-calibration", "xy-dc-voltage-calibration", "z-field-bandwidth",
    "aw-const-omega-check", "aw-const-omega-awscale-check",
})
# shortcut: 只检查显式锁相频率参数及下方标定窗口，新增派生扫频时需补充窗口检查。
FREQUENCY_KEYS = frozenset({
    "HF2_OSC_FREQ", "HF2_OSC_FREQ_HZ", "HF2_REFERENCE_FREQUENCY_HZ",
    "PUMP_MOD_FREQ", "PUMP_MOD_FREQ_HZ", "DEMOD0_OSC_FREQ",
    "HF2_DEMOD0_OSC_FREQ", "HF2_DEMOD0_OSC_FREQ_HZ", "DEMOD0_OSC_FREQ_HZ",
    "Z_RF_FREQ", "Z_RF_NOISE_FREQ", "Z_RF_FREQ_HZ", "Z_RF_FREQS_HZ", "RF_FREQS_HZ",
    "RF_FREQUENCIES_HZ", "Y_RF_FREQUENCY_HZ", "MAIN_FIELD_REFERENCE_FREQUENCY_HZ",
    "ZERO_BIAS_REFERENCE_FREQUENCY_HZ", "ZERO_BIAS_CENTER_FREQUENCY_HZ",
    "FREQUENCY_START_HZ", "FREQUENCY_STOP_HZ", "RF_FREQUENCY_START_HZ", "RF_FREQUENCY_STOP_HZ",
    "DEMOD_FREQUENCY_HZ", "KEITHLEY_REFERENCE_FREQUENCY_HZ",
})


def lockin_preflight(experiment_id: str, values: dict[str, Any], root: Path | None = None,
                     *, mapping: dict | None = None) -> list[str]:
    from .experiments import get_experiment
    try:
        definition = get_experiment(experiment_id)
    except KeyError:
        definition = None
    if definition and definition.required_mapping_keys and not any(key.startswith("lockin_") for key in definition.required_mapping_keys):
        return []
    if mapping is None:
        try:
            mapping = load_mapping(root)
        except Exception as exc:
            return [f"锁相放大器映射无效: {exc}"]
    config = mapping.get("lockin_r") or mapping.get("lockin_xy")
    if not config:
        return []
    model = str(config.get("model", "HF2")).upper()
    declared = config.get("capabilities", {})
    try:
        caps = lockin_capabilities(model, declared.get("options", []))
    except ValueError as exc:
        return [str(exc)]
    errors = []
    from .lockin_acquisition import experiment_acquisition_requests, acquisition_preflight
    try:
        acquisition_preflight(config, experiment_acquisition_requests(experiment_id, values))
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    rf_direct_aw = experiment_id in {"rf-sensitivity-aw", "rf-sensitivity-aw-r", "rf-sensitivity-aw-known-noise"}
    if rf_direct_aw:
        if caps["demodulators"] < 2 or caps["oscillators"] < 2:
            errors.append(f"{model} 缺少独立 RF 解调器或振荡器；MFLI 需要 MF-MD 选件")
        route = values.get("RF_INPUT_ROUTE", "physical")
        output = values.get("RF_AUX_OUTPUT_IDX", 1)
        source = values.get("RF_PHYSICAL_INPUT_SOURCE", "voltage1")
        if route not in {"physical", "internal"}:
            errors.append("RF 输入线路必须为 physical 或 internal")
        if isinstance(output, bool) or not isinstance(output, int) or not 0 <= output < caps["aux_outputs"]:
            errors.append("RF 辅助输出通道必须为 AuxOut1–4（索引 0–3）")
        if route == "internal" and model != "MFLI":
            errors.append("HF2 不支持内部 AuxOut 回采，请选择物理连接")
        if route == "physical" and source not in ({"voltage1", "aux_input0", "aux_input1"} if model == "HF2" else {"aux_input0", "aux_input1"}):
            errors.append(f"{model} 不支持所选 RF 物理输入 {source}，请明确选择辅助输入通道；不会自动更换接线")
        if values.get("DEMOD3_IDX", 3) == values.get("DEMOD0_IDX", 0):
            errors.append("RF 解调器不能占用主解调器 0")
    if experiment_id in DUAL_VOLTAGE_EXPERIMENTS and not rf_direct_aw and caps["voltage_inputs"] < 2:
        errors.append(f"{experiment_id} 需要两路独立主电压输入：{model} 只有一路。Aux Input 回采尚未验证，当前禁止启动。")
    for name, value in values.items():
        key = name.upper()
        if re.fullmatch(r"(?:HF2_)?DEMOD(?:[0-7]|_R)?_IDX|AUXOUT_SOURCE_DEMOD_IDX", key):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < caps["demodulators"]:
                errors.append(f"{name}={value} 超出 {model} 解调器范围 0–{caps['demodulators'] - 1}")
        if re.fullmatch(r"(?:HF2_)?DEMOD(?:[0-7]|_R)?_OSC_IDX", key):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < caps["oscillators"]:
                errors.append(f"{name}={value} 超出 {model} 振荡器范围")
        if key in FREQUENCY_KEYS:
            try:
                frequencies = np.asarray(value, dtype=float)
                valid = np.isfinite(frequencies).all() and ((frequencies >= 0) & (frequencies <= caps["max_frequency_hz"])).all()
            except (TypeError, ValueError):
                valid = False
            if not valid:
                errors.append(f"{name} 超出 {model} 的 0–{caps['max_frequency_hz']:g} Hz 范围")
    try:
        centers = None
        if experiment_id == "bell-bloom-z-field-calibration":
            from .experiment_modules.bell_bloom_z_field_calibration.models import BellBloomZFieldCalibrationParams
            params = BellBloomZFieldCalibrationParams.from_external(values)
            centers = [params.predicted_center(value) for value in (params.z_bias_start_v, params.z_bias_stop_v)]
        elif experiment_id in {"mx-z-field-calibration", "mx-z-current-coupling-calibration"}:
            from .experiment_modules.mx_z_field_calibration.models import MxZFieldCalibrationParams
            from .experiment_modules.mx_z_field_calibration.scan import predicted_center_hz
            if experiment_id == "mx-z-current-coupling-calibration":
                from .experiment_modules.mx_z_current_coupling_calibration.models import MxZCurrentCouplingCalibrationParams
                params = MxZCurrentCouplingCalibrationParams.from_external(values)
            else:
                params = MxZFieldCalibrationParams.from_external(values)
            centers = [predicted_center_hz(params, value) for value in (params.z_bias_start_v, params.z_bias_stop_v)]
        elif experiment_id == "mx-main-field-calibration":
            from .experiment_modules.mx_main_field_calibration.models import MxMainFieldCalibrationParams
            from .experiment_modules.mx_main_field_calibration.scan import predicted_center_hz
            params = MxMainFieldCalibrationParams.from_external(values)
            centers = [predicted_center_hz(params, value) for value in (params.main_field_start_ma, params.main_field_stop_ma)]
        elif experiment_id == "mx-keithley-6221-main-field-calibration":
            from .experiment_modules.mx_keithley_6221_main_field_calibration.models import MxKeithley6221MainFieldCalibrationParams
            from .experiment_modules.mx_keithley_6221_main_field_calibration.scan import predicted_center_hz
            params = MxKeithley6221MainFieldCalibrationParams.from_external(values)
            centers = [predicted_center_hz(params, value) for value in (params.keithley_current_start_ma, params.keithley_current_stop_ma)]
        if centers is not None:
            half_width = params.frequency_half_width_hz
            bounds = np.asarray(centers) + np.array([[-half_width], [half_width]])
            if not np.isfinite(bounds).all() or (bounds <= 0).any() or (bounds > caps["max_frequency_hz"]).any():
                errors.append(f"完整扫频窗口超出 {model} 的 0–{caps['max_frequency_hz']:g} Hz 范围")
    except (KeyError, TypeError, ValueError):
        errors.append("无法计算锁相完整扫频窗口，请检查标定参数")
    return errors


def require_lockin_compatibility(experiment_id: str, values: dict[str, Any], *, mapping: dict | None = None) -> None:
    errors = lockin_preflight(experiment_id, values, mapping=mapping)
    if errors:
        raise ValueError("；".join(errors))
    if mapping is not None:
        from dataclasses import asdict
        from .lockin_acquisition import experiment_acquisition_requests
        requests = [asdict(request) for request in experiment_acquisition_requests(experiment_id, values)]
        for config in mapping.values():
            if config.get("instrument") == "lockin_amplifier":
                config["_acquisition_requests"] = requests
