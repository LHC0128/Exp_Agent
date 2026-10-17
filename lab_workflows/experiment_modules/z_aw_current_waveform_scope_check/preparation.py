"""加载电流目标、冻结来源并复用已验收的 Z 任意波配置。"""
from dataclasses import replace
import shutil

import numpy as np

from ...control_sources import corrected_control_contract
from ...current_feedback import (
    load_corrected_control_waveform, load_current_coupling_calibration,
    sha256_file, target_current_from_omega, validate_current_power,
)
from ...steps import save_corrected_control_source_snapshot, save_optimal_control_source_snapshot
from ..z_aw_waveform_scope_check.preparation import preflight, prepare_control as prepare_voltage_control


def prepare_control(params, root):
    calibration = load_current_coupling_calibration(root, params.current_coupling_calibration_source_run)
    if not np.isclose(calibration.sense_resistor_ohm, params.sense_resistor_ohm, rtol=1e-6, atol=1e-12):
        raise ValueError("实验参数中的采样电阻与电流耦合标定来源不一致")
    if params.control_waveform_source == "corrected_run":
        corrected = load_corrected_control_waveform(root, params.corrected_control_source_run)
        if (corrected.coupling_calibration_run != calibration.run_name or
                corrected.coupling_calibration_sha256 != calibration.analysis_sha256):
            raise ValueError("冻结波形与选择的电流耦合标定运行或哈希不一致")
        theory, applied = corrected_control_contract(corrected)
        source = prepare_voltage_control(params, root, measurement_mode="current",
            current_calibration=calibration, theory_override=theory, applied_override=applied,
            target_control_scale=1.0)
        source.target_current_a = np.array(corrected.target_current_a, copy=True)
        inputs = [(corrected.waveform_path, corrected.waveform_sha256)]
        writer = lambda raw: save_corrected_control_source_snapshot(raw, corrected, applied)
        copied = [("source_corrected_control_waveform.npz", corrected.waveform_sha256)]
    elif params.control_waveform_source == "theory":
        source = prepare_voltage_control(
            replace(params, z_calibration_source_run=params.command_z_calibration_source_run),
            root, measurement_mode="current", current_calibration=calibration)
        source.target_current_a = target_current_from_omega(params.control_scale * source.theory.omega_ctrl_hz,
                                                          calibration)
        inputs = [(source.theory.waveform_path, source.theory.waveform_sha256),
                  (source.theory.parameter_path, source.theory.parameter_sha256),
                  (source.calibration.analysis_path, source.calibration.analysis_sha256)]
        writer = lambda raw: save_optimal_control_source_snapshot(raw, source.theory, source.calibration,
                                                                 source.applied)
        copied = list(zip(("source_optimal_control_waveform.csv", "source_optimal_control_params.csv",
                           "source_z_calibration_analysis.yaml"), (digest for _, digest in inputs)))
    else:
        raise ValueError("CONTROL_WAVEFORM_SOURCE 必须是 theory 或 corrected_run")
    validate_current_power(source.target_current_a, params.sense_resistor_ohm,
        params.sense_resistor_power_rating_w, derating_fraction=0.5, maximum_current_a=params.maximum_current_a)
    inputs.append((calibration.analysis_path, calibration.analysis_sha256))
    copied.append(("source_current_coupling_analysis.yaml", calibration.analysis_sha256))

    def snapshot(raw):
        for path, digest in inputs:
            if sha256_file(path) != digest:
                raise ValueError("控制源或电流标定已改变，请重新执行 A03")
        files = writer(raw)
        shutil.copy2(calibration.analysis_path, raw / "source_current_coupling_analysis.yaml")
        for name, digest in copied:
            if sha256_file(raw / name) != digest:
                raise ValueError("冻结来源副本哈希不一致，停止采集")
        return [*files, "raw/source_current_coupling_analysis.yaml"]

    source.source_snapshot_writer = snapshot
    return source
