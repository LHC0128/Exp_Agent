"""高通链路的局部谐波辨识、独立验收与真实回退。"""

import json
import numpy as np

from ...current_feedback import sha256_array
from ...experiment_runtime import check_cancelled
from .harmonic_feedback import (
    MeasurementQualityError, acceptance_margin, bounded_update, coefficients,
    harmonic_basis, require_repeatability,
)


def identify(command, reference, basis, prepared, params, path, acquire, upload):
    """每个 cos/sin 系数做正负扰动，保存全部原始采集与中心差分矩阵。"""
    path.mkdir()
    columns, amplitudes, snrs = [], [], []
    for column in range(basis.shape[1]):
        amplitude = params.identification_step_v
        attempt = 0
        while True:
            check_cancelled()
            pair = []
            for sign, name in ((1, "plus"), (-1, "minus")):
                candidate = command + sign * amplitude * basis[:, column]
                pair.append(acquire(candidate, path / f"coefficient_{column:03d}_{attempt:02d}_{name}", params.identification_repeats))
            plus, minus = pair
            difference = coefficients(plus["measured"] - minus["measured"], basis)
            # 逐帧系数的均值标准误，包含正负两组的重复性。
            noise = np.sqrt(np.sum([
                np.sum(np.var(coefficients(item["frames"], basis), axis=0, ddof=1)) / len(item["frames"])
                for item in pair
            ]))
            snr = float(np.linalg.norm(difference) / max(noise, prepared.target_rms_a * 1e-6))
            if snr >= params.identification_min_snr:
                columns.append(difference / (2 * amplitude))
                amplitudes.append(amplitude)
                snrs.append(snr)
                break
            if amplitude >= params.identification_maximum_step_v:
                raise MeasurementQualityError(f"第 {column + 1} 个辨识方向信噪比 {snr:.2f} 不足，未更新")
            amplitude = min(amplitude * 2, params.identification_maximum_step_v)
            attempt += 1
    restored = acquire(command, path / "restored_baseline", params.holdout_repeats)
    require_repeatability(reference, restored, basis, params, prepared.target_rms_a)
    jacobian = np.column_stack(columns)
    np.savez(path / "jacobian.npz", jacobian_a_per_v=jacobian,
             basis=basis, perturbation_v=amplitudes, difference_snr=snrs,
             singular_values=np.linalg.svd(jacobian, compute_uv=False),
             command_voltage_v=command, phase_reference="CH4_falling_edge")
    return jacobian, restored


def run_harmonic_loop(prepared, params, run_dir, records, acquire, upload, log):
    """回调沿用统一设备与安全收尾；本函数只管理闭环状态。"""
    command = prepared.initial.voltage_v.copy()
    bins = np.flatnonzero(prepared.learning_band)
    basis = harmonic_basis(command.size, bins)
    run_dir.update_config(
        phase_reference="CH4_falling_edge", dc_learning=False,
        selected_harmonics_hz=prepared.frequency_hz[bins].tolist(),
        current_frequency_response={"mode": "local_harmonic_jacobian", "effective_cutoff_hz": float(prepared.frequency_hz[bins].max())},
        final_reload_validated=False,
    )

    def record(measurement, voltage, path, accepted, phase, backtrack=0):
        row = {"iteration": len(records), "relative_rms_error": measurement["score"],
               "error_rms_a": measurement["error_rms_a"],
               "in_band_error_rms_a": measurement["in_band_error_rms_a"],
               "outside_band_error_rms_a": measurement["outside_band_error_rms_a"],
               "score_sem": measurement["score_sem"], "accepted": accepted,
               "phase": phase, "backtrack": backtrack, "raw_correction_peak_v": float(raw_peak) if "raw_peak" in locals() else None,
               "measurement_path": str(path.relative_to(run_dir.raw)).replace("\\", "/"),
               "command_sha256": sha256_array(voltage)}
        records.append(row)
        log(f"{phase}：完整波形误差 {measurement['score']:.3%}，{'接受' if accepted else '拒绝并回退'}")
        return row

    log("基线测量与关闭/重新上传重复性检查")
    first = acquire(command, run_dir.raw / "baseline_initial", params.holdout_repeats)
    path = run_dir.raw / "iteration_000"
    baseline = acquire(command, path, params.holdout_repeats)
    require_repeatability(first, baseline, basis, params, prepared.target_rms_a)
    best_row = record(baseline, command, path, True, "baseline")
    best, best_command = baseline, command.copy()
    passes = int(baseline["score"] <= params.target_relative_rms)
    jacobian = None
    stop_reason = "达到最大轮数"
    for iteration in range(1, params.max_iterations):
        check_cancelled()
        if passes >= params.required_passes:
            stop_reason = "达标"
            break
        if baseline["score"] <= params.target_relative_rms:
            # 达标后保持同一命令，用新的独立采集确认连续通过。
            path = run_dir.raw / f"iteration_{len(records):03d}"
            checked = acquire(command, path, params.holdout_repeats)
            require_repeatability(baseline, checked, basis, params, prepared.target_rms_a)
            row = record(checked, command, path, True, "threshold_confirmation")
            baseline = checked
            passes = passes + 1 if checked["score"] <= params.target_relative_rms else 0
            if checked["score"] < best["score"]:
                best, best_command, best_row = checked, command.copy(), row
            continue
        feedback = acquire(command, run_dir.raw / f"feedback_{iteration:03d}", params.iteration_repeats)
        require_repeatability(baseline, feedback, basis, params, prepared.target_rms_a)
        if jacobian is None or (iteration - 1) % params.reidentify_interval == 0:
            log(f"辨识 {len(bins)} 个谐波，共 {basis.shape[1]} 个 cos/sin 方向")
            jacobian, restored = identify(command, feedback, basis, prepared, params,
                                           run_dir.raw / f"identification_{iteration:03d}", acquire, upload)
            require_repeatability(baseline, restored, basis, params, prepared.target_rms_a)
        raw_increment = bounded_update(jacobian, feedback["error"], basis,
                                       params.inverse_regularization, params.iteration_damping, 1e99)
        raw_peak = float(np.max(np.abs(raw_increment)))
        increment = raw_increment * min(1.0, params.maximum_step_v / max(raw_peak, 1e-30))
        log(f"局部逆修正峰值 {raw_peak:.4g} V，实际步长 {np.max(np.abs(increment)):.4g} V")
        accepted = False
        for backtrack in range(params.maximum_backtracks + 1):
            check_cancelled()
            candidate = command + increment / (2 ** backtrack)
            path = run_dir.raw / f"iteration_{len(records):03d}"
            candidate_result = acquire(candidate, path, params.holdout_repeats)
            margin = acceptance_margin(baseline, candidate_result, params)
            accepted = candidate_result["score"] < baseline["score"] - margin
            row = record(candidate_result, candidate, path, accepted, "candidate", backtrack)
            row["required_improvement"] = margin
            np.savez(path / "update.npz", baseline_voltage_v=command,
                     increment_voltage_v=increment / (2 ** backtrack), candidate_voltage_v=candidate)
            if accepted:
                command, baseline = candidate, candidate_result
                if baseline["score"] < best["score"]:
                    best, best_command, best_row = baseline, command.copy(), row
                passes = int(baseline["score"] <= params.target_relative_rms)
                break
            # 真实恢复硬件；下一次候选仍由同一个已接受基线构造。
            upload(command, path / "rollback")
        if not accepted:
            stop_reason = "回退次数耗尽，保持已验证最佳命令"
            break
    if passes >= params.required_passes:
        stop_reason = "达标"
    log("关闭输出后重新上传最佳命令，独立采集最终验证")
    final_path = run_dir.raw / "final_validation"
    final = acquire(best_command, final_path, params.final_validation_repeats)
    require_repeatability(best, final, basis, params, prepared.target_rms_a)
    margin = acceptance_margin(best, final, params)
    valid = final["score"] <= best["score"] + margin
    validation = {**best_row, "relative_rms_error": final["score"],
                  "error_rms_a": final["error_rms_a"],
                  "in_band_error_rms_a": final["in_band_error_rms_a"],
                  "outside_band_error_rms_a": final["outside_band_error_rms_a"],
                  "score_sem": final["score_sem"],
                  "measurement_path": "final_validation", "validated": bool(valid)}
    (final_path / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    run_dir.update_config(final_reload_validated=bool(valid), final_reload_relative_rms_error=final["score"],
                          selected_iteration=best_row["iteration"])
    if not valid:
        raise MeasurementQualityError("最佳命令重新加载后误差恶化，保留原始数据但不生成可复用冻结波形")
    if stop_reason == "达标" and final["score"] > params.target_relative_rms:
        stop_reason = "最终重载未达阈值"
    return stop_reason, validation
