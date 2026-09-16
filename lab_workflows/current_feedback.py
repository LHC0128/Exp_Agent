"""Z 线圈实际电流反馈、耦合标定和校正波形共享数据契约。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml


_RUN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_DG4000_ARB_POINTS = 16384


@dataclass(frozen=True, slots=True)
class CurrentCouplingCalibration:
    """实际线圈电流到控制耦合强度的标定结果。"""

    run_name: str
    analysis_path: Path
    slope_hz_per_a: float
    slope_hz_per_vsense: float
    intercept_hz: float
    sense_resistor_ohm: float
    r_squared: float
    analysis_sha256: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CorrectedControlWaveform:
    """闭环校正实验冻结的 DG 任意波。"""

    run_name: str
    waveform_path: Path
    time_s: np.ndarray
    omega_ctrl_hz: np.ndarray
    target_current_a: np.ndarray
    normalized: np.ndarray
    voltage_v: np.ndarray
    repeat_frequency_hz: float
    amplitude_vpp: float
    offset_v: float
    waveform_sha256: str
    coupling_calibration_run: str
    coupling_calibration_sha256: str
    frequency_response_run: str
    frequency_response_sha256: str
    # v3 冻结文件新增；旧格式读取时保持 0。
    error_cutoff_hz: float = 0.0
    inverse_regularization: float = 0.0


# 电流频响标定的数据契约：对数扫频正弦协议，闭环按目标周期网格插值取用。
AW_CURRENT_RESPONSE_FORMAT_VERSION = 4
AW_CURRENT_RESPONSE_PROTOCOL = "coherent_swept_sine_current_response"
AW_CURRENT_RESPONSE_COMMAND_REFERENCE = "50_ohm"
# 相位参考必须是与电流同一帧的实测驱动电压；否则相位含相干启动时延，无法插值。
AW_CURRENT_RESPONSE_PHASE_REFERENCE = "measured_drive_voltage"


@dataclass(frozen=True, slots=True)
class AWCurrentResponse:
    """对数扫频测得的实际电流复响应，含逐点可靠性掩码。

    频响不再与任何目标 AW 网格绑定：闭环在可靠频点之间按目标波形的周期
    网格插值取用，不跨不可靠点、不外推。
    """

    run_name: str
    response_path: Path
    frequency_hz: np.ndarray
    transfer_a_per_v: np.ndarray
    reliable: np.ndarray
    sense_resistor_ohm: float
    response_sha256: str


@dataclass(frozen=True, slots=True)
class CurrentFrequencyResponse:
    """实际线圈电流复数频响的可靠频点。"""

    run_name: str
    response_path: Path
    frequency_hz: np.ndarray
    transfer_a_per_v: np.ndarray
    sense_resistor_ohm: float
    response_sha256: str


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(values: Any) -> str:
    """按连续 float64 字节计算数组哈希，便于追踪每轮命令波形。"""
    array = np.ascontiguousarray(np.asarray(values, dtype=np.float64))
    return hashlib.sha256(array.tobytes()).hexdigest()


def _validated_run_name(run_name: str, label: str) -> str:
    value = str(run_name).strip()
    if not _RUN_PATTERN.fullmatch(value):
        raise ValueError(f"{label} 只能包含字母、数字、点、下划线和短横线")
    return value


def validate_sense_resistor(
    resistance_ohm: float,
    power_rating_w: float,
    *,
    tolerance_percent: float | None = None,
) -> None:
    """校验采样电阻的实测阻值、功率和可选误差。"""
    values = (float(resistance_ohm), float(power_rating_w))
    if not all(np.isfinite(value) for value in values):
        raise ValueError("采样电阻参数必须是有限数值")
    if resistance_ohm <= 0.0:
        raise ValueError("SENSE_RESISTOR_OHM 必须填写实测正阻值")
    if power_rating_w <= 0.0:
        raise ValueError("SENSE_RESISTOR_POWER_RATING_W 必须填写正额定功率")
    if tolerance_percent is not None:
        tolerance = float(tolerance_percent)
        if not np.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError("SENSE_RESISTOR_TOLERANCE_PERCENT 必须为非负有限值")


def sense_voltage_to_current(voltage_v: Any, resistance_ohm: float) -> np.ndarray:
    """把低端采样电阻电压换算为线圈电流。"""
    validate_sense_resistor(resistance_ohm, 1.0)
    voltage = np.asarray(voltage_v, dtype=float)
    if not np.all(np.isfinite(voltage)):
        raise ValueError("采样电阻电压包含非有限值")
    return voltage / float(resistance_ohm)


def validate_current_power(
    current_a: Any,
    resistance_ohm: float,
    power_rating_w: float,
    *,
    derating_fraction: float,
    maximum_current_a: float,
) -> dict[str, float]:
    """按峰值电流和 RMS 功率校验采样电阻及用户电流上限。"""
    validate_sense_resistor(resistance_ohm, power_rating_w)
    current = np.asarray(current_a, dtype=float).reshape(-1)
    if current.size == 0 or not np.all(np.isfinite(current)):
        raise ValueError("电流波形必须包含有限样本")
    if not np.isfinite(derating_fraction) or not 0.0 < derating_fraction <= 1.0:
        raise ValueError("SENSE_RESISTOR_POWER_DERATING 必须在 (0, 1] 内")
    if not np.isfinite(maximum_current_a) or maximum_current_a <= 0.0:
        raise ValueError("MAXIMUM_CURRENT_A 必须填写正安全上限")
    peak_a = float(np.max(np.abs(current)))
    rms_a = float(np.sqrt(np.mean(current * current)))
    power_w = float(rms_a * rms_a * resistance_ohm)
    allowed_power_w = float(power_rating_w * derating_fraction)
    if peak_a > maximum_current_a + 1e-15:
        raise ValueError(
            f"预测峰值电流 {peak_a:.6g} A 超过 MAXIMUM_CURRENT_A="
            f"{maximum_current_a:.6g} A"
        )
    if power_w > allowed_power_w + 1e-15:
        raise ValueError(
            f"预测采样电阻 RMS 功率 {power_w:.6g} W 超过降额上限 "
            f"{allowed_power_w:.6g} W"
        )
    return {
        "peak_current_a": peak_a,
        "rms_current_a": rms_a,
        "sense_resistor_power_w": power_w,
        "sense_resistor_allowed_power_w": allowed_power_w,
    }


def load_current_coupling_calibration(
    project_root: Path,
    run_name: str,
) -> CurrentCouplingCalibration:
    """读取成功的实际电流-耦合强度标定。"""
    run_name = _validated_run_name(run_name, "CURRENT_COUPLING_CALIBRATION_SOURCE_RUN")
    path = (
        Path(project_root)
        / "data"
        / "Mx_Z_Current_Coupling_Calibration"
        / str(run_name)
        / "results"
        / "analysis.yaml"
    ).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到电流耦合标定结果: {path}")
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if payload.get("experiment_id") != "mx-z-current-coupling-calibration":
        raise ValueError(f"电流耦合标定 experiment_id 不匹配: {path}")
    if payload.get("success") is not True:
        raise ValueError(f"电流耦合标定未通过质量检查: {path}")
    slope = float(payload["K_Z_Hz_per_A"])
    slope_vsense = float(payload["K_Z_Hz_per_Vsense"])
    intercept = float(payload["f_0_at_zero_current_hz"])
    resistance = float(payload["sense_resistor_ohm"])
    r_squared = float(payload["linearity"]["r_squared"])
    if not all(
        np.isfinite(value)
        for value in (slope, slope_vsense, intercept, resistance, r_squared)
    ):
        raise ValueError("电流耦合标定包含非有限值")
    if slope == 0.0 or slope_vsense == 0.0 or resistance <= 0.0:
        raise ValueError("电流耦合标定斜率和采样电阻必须非零")
    return CurrentCouplingCalibration(
        run_name=str(run_name),
        analysis_path=path,
        slope_hz_per_a=slope,
        slope_hz_per_vsense=slope_vsense,
        intercept_hz=intercept,
        sense_resistor_ohm=resistance,
        r_squared=r_squared,
        analysis_sha256=sha256_file(path),
        payload=payload,
    )


def target_current_from_omega(
    omega_ctrl_hz: Any,
    calibration: CurrentCouplingCalibration,
) -> np.ndarray:
    """按标定自由斜率把目标耦合强度换算为实际线圈电流。"""
    omega = np.asarray(omega_ctrl_hz, dtype=float)
    if not np.all(np.isfinite(omega)):
        raise ValueError("目标控制耦合强度包含非有限值")
    return omega / calibration.slope_hz_per_a


def load_current_frequency_response(
    project_root: Path,
    run_name: str,
) -> CurrentFrequencyResponse:
    """读取实际电流频响并仅返回分析标记为可靠的频点。"""
    run_name = _validated_run_name(
        run_name, "CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN"
    )
    path = (
        Path(project_root)
        / "data"
        / "Z_Coil_Current_Frequency_Response"
        / run_name
        / "results"
        / "frequency_response.npz"
    ).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到实际电流频响结果: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "frequency_hz",
            "transfer_real_a_per_v_mean",
            "transfer_imag_a_per_v_mean",
            "reliable",
            "sense_resistor_ohm",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"实际电流频响缺少字段: {', '.join(missing)}")
        frequency = np.asarray(data["frequency_hz"], dtype=float).reshape(-1)
        real = np.asarray(data["transfer_real_a_per_v_mean"], dtype=float).reshape(-1)
        imag = np.asarray(data["transfer_imag_a_per_v_mean"], dtype=float).reshape(-1)
        reliable = np.asarray(data["reliable"], dtype=bool).reshape(-1)
        resistance = float(np.asarray(data["sense_resistor_ohm"]).reshape(()))
    if not (frequency.size == real.size == imag.size == reliable.size):
        raise ValueError("实际电流频响数组长度不一致")
    valid = (
        reliable
        & np.isfinite(frequency)
        & np.isfinite(real)
        & np.isfinite(imag)
        & (frequency > 0.0)
    )
    if np.count_nonzero(valid) < 2:
        raise ValueError("实际电流频响可靠频点不足 2 个")
    if not np.isfinite(resistance) or resistance <= 0.0:
        raise ValueError("实际电流频响的采样电阻无效")
    order = np.argsort(frequency[valid])
    return CurrentFrequencyResponse(
        run_name=run_name,
        response_path=path,
        frequency_hz=frequency[valid][order],
        transfer_a_per_v=(real[valid] + 1j * imag[valid])[order],
        sense_resistor_ohm=resistance,
        response_sha256=sha256_file(path),
    )


def load_aw_frequency_response(project_root: Path, run_name: str) -> AWCurrentResponse:
    """读取相干扫频测得的实际电流复响应。

    只接受对数扫频正弦协议（``format_version=4``、实测驱动电压相位参考）的标定。
    逐谐波 AW 网格协议（``format_version=3``）把标定与某一个目标波形强绑定，
    闭环不再支持，需要按当前协议重新标定。
    """
    run_name = _validated_run_name(run_name, "CURRENT_FREQUENCY_RESPONSE_SOURCE_RUN")
    path = (
        Path(project_root)
        / "data"
        / "Z_Coil_Current_Frequency_Response"
        / run_name
        / "results"
        / "frequency_response.npz"
    ).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到电流频响结果: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "format_version", "output_protocol", "command_voltage_reference",
            "phase_reference", "frequency_hz", "transfer_real_a_per_v",
            "transfer_imag_a_per_v", "reliable", "sense_resistor_ohm",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"电流频响缺少字段: {', '.join(missing)}")
        format_version = int(np.asarray(data["format_version"]).reshape(()))
        if format_version != AW_CURRENT_RESPONSE_FORMAT_VERSION:
            raise ValueError(
                "电流频响不是对数扫频正弦协议 "
                f"format_version={AW_CURRENT_RESPONSE_FORMAT_VERSION} 文件"
                f"（读到 {format_version}），请按当前协议重新标定")
        protocol = str(np.asarray(data["output_protocol"]).reshape(()))
        if protocol != AW_CURRENT_RESPONSE_PROTOCOL:
            raise ValueError(f"电流频响输出协议不匹配: {protocol}")
        reference = str(np.asarray(data["command_voltage_reference"]).reshape(()))
        if reference != AW_CURRENT_RESPONSE_COMMAND_REFERENCE:
            raise ValueError(
                "电流频响命令电压基准必须是 "
                f"{AW_CURRENT_RESPONSE_COMMAND_REFERENCE}")
        phase_reference = str(np.asarray(data["phase_reference"]).reshape(()))
        if phase_reference != AW_CURRENT_RESPONSE_PHASE_REFERENCE:
            raise ValueError(
                "电流频响的相位参考不是实测驱动电压，相位含相干启动时延，"
                "不能用于闭环插值")
        frequency = np.asarray(data["frequency_hz"], dtype=float).reshape(-1)
        real = np.asarray(data["transfer_real_a_per_v"], dtype=float).reshape(-1)
        imag = np.asarray(data["transfer_imag_a_per_v"], dtype=float).reshape(-1)
        reliable = np.asarray(data["reliable"], dtype=bool).reshape(-1)
        resistance = float(np.asarray(data["sense_resistor_ohm"]).reshape(()))
    count = frequency.size
    if not (count == real.size == imag.size == reliable.size):
        raise ValueError("电流频响数组长度不一致")
    if count < 2:
        raise ValueError("电流频响至少需要两个扫频点")
    if not np.all(np.isfinite(frequency)) or np.any(frequency <= 0.0):
        raise ValueError("电流频响频率必须为正有限值")
    if np.any(np.diff(frequency) <= 0.0):
        raise ValueError("电流频响频率必须严格递增")
    if np.count_nonzero(reliable) < 2:
        raise ValueError("电流频响可靠频点不足 2 个")
    if not np.all(np.isfinite(real[reliable])) or not np.all(np.isfinite(imag[reliable])):
        raise ValueError("电流频响可靠频点包含非有限值")
    if not np.isfinite(resistance) or resistance <= 0.0:
        raise ValueError("电流频响的采样电阻无效")
    return AWCurrentResponse(
        run_name=str(run_name),
        response_path=path,
        frequency_hz=frequency,
        transfer_a_per_v=real + 1j * imag,
        reliable=reliable,
        sense_resistor_ohm=resistance,
        response_sha256=sha256_file(path),
    )


def load_corrected_control_waveform(
    project_root: Path,
    run_name: str,
) -> CorrectedControlWaveform:
    """读取并严格校验闭环实验冻结的任意波。"""
    run_name = _validated_run_name(run_name, "CORRECTED_CONTROL_SOURCE_RUN")
    path = (
        Path(project_root)
        / "data"
        / "Z_AW_Closed_Loop_Waveform_Correction"
        / str(run_name)
        / "results"
        / "corrected_control_waveform.npz"
    ).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"未找到闭环校正波形: {path}")
    with np.load(path, allow_pickle=False) as data:
        required = {
            "time_s",
            "omega_ctrl_hz",
            "target_current_a",
            "normalized",
            "voltage_v",
            "repeat_frequency_hz",
            "amplitude_vpp",
            "offset_v",
            "coupling_calibration_run",
            "coupling_calibration_sha256",
        }
        missing = sorted(required.difference(data.files))
        if missing:
            raise ValueError(f"校正波形缺少字段: {', '.join(missing)}")
        time_s = np.asarray(data["time_s"], dtype=float).reshape(-1)
        omega = np.asarray(data["omega_ctrl_hz"], dtype=float).reshape(-1)
        target_current = np.asarray(data["target_current_a"], dtype=float).reshape(-1)
        normalized = np.asarray(data["normalized"], dtype=float).reshape(-1)
        voltage = np.asarray(data["voltage_v"], dtype=float).reshape(-1)
        repeat_frequency = float(np.asarray(data["repeat_frequency_hz"]).reshape(()))
        amplitude = float(np.asarray(data["amplitude_vpp"]).reshape(()))
        offset = float(np.asarray(data["offset_v"]).reshape(()))
        coupling_run = str(np.asarray(data["coupling_calibration_run"]).reshape(()))
        coupling_sha256 = str(
            np.asarray(data["coupling_calibration_sha256"]).reshape(())
        )
        # 集中兼容各历史格式：v3 响应滤波闭环、v2 静态闭环、更早的频域闭环。
        version = int(data["format_version"]) if "format_version" in data.files else 1
        if version not in {1, 2, 3, 4}:
            raise ValueError("不支持的冻结波形格式版本")
        error_cutoff_hz, inverse_regularization = 0.0, 0.0
        if version >= 2:
            response_run, response_sha256 = "", ""
        else:
            response_run = str(np.asarray(data["frequency_response_run"]).reshape(()))
            response_sha256 = str(np.asarray(data["frequency_response_sha256"]).reshape(()))
        if version in {3, 4}:
            response_run = str(np.asarray(data["frequency_response_run"]).reshape(()))
            response_sha256 = str(np.asarray(data["frequency_response_sha256"]).reshape(()))
            error_cutoff_hz = float(np.asarray(data["error_cutoff_hz"]).reshape(()))
            inverse_regularization = float(
                np.asarray(data["inverse_regularization"]).reshape(()))
            method = str(data["correction_method"]) if "correction_method" in data.files else "response_filtered_feedback"
            needs_response = version == 3 and method != "time_domain"
            if (needs_response and not response_run) or not 0.0 < inverse_regularization <= 1.0:
                raise ValueError("冻结波形 v3 的响应来源或正则化参数无效")
            if not np.isfinite(error_cutoff_hz) or error_cutoff_hz <= 0:
                raise ValueError("冻结波形学习频率无效")
            if version == 4:
                required_v4 = {"final_reload_validated", "phase_reference", "identification_protocol", "measured_current_a", "relative_rms_error"}
                if not required_v4.issubset(data.files):
                    raise ValueError("冻结波形 v4 缺少独立重载验证字段")
                if (method != "harmonic_jacobian" or not bool(data["final_reload_validated"])
                        or str(data["phase_reference"]) != "CH4_falling_edge"
                        or str(data["identification_protocol"]) != "central_difference_harmonic_jacobian"):
                    raise ValueError("冻结波形 v4 未通过固定 CH4 参考的独立重载验证")
                measured = np.asarray(data["measured_current_a"], dtype=float)
                if measured.shape != target_current.shape or not np.all(np.isfinite(measured)):
                    raise ValueError("冻结波形 v4 实测数组无效")
                target_rms = np.sqrt(np.mean(target_current ** 2))
                score = np.sqrt(np.mean((target_current - measured) ** 2)) / max(target_rms, 1e-30)
                if target_rms == 0 or not np.isclose(score, float(data["relative_rms_error"]), rtol=1e-7, atol=1e-10):
                    raise ValueError("冻结波形 v4 实测波形与验收指标不一致")
    sizes = {array.size for array in (time_s, omega, target_current, normalized, voltage)}
    if len(sizes) != 1 or time_s.size < 2:
        raise ValueError("校正波形数组长度不一致或点数不足")
    if time_s.size > _MAX_DG4000_ARB_POINTS:
        raise ValueError(
            f"校正波形点数 {time_s.size} 超过 DG4000 上限 {_MAX_DG4000_ARB_POINTS}"
        )
    if not all(
        np.all(np.isfinite(array))
        for array in (time_s, omega, target_current, normalized, voltage)
    ):
        raise ValueError("校正波形包含非有限值")
    steps = np.diff(time_s)
    if np.any(steps <= 0.0) or not np.allclose(
        steps,
        np.median(steps),
        rtol=1e-6,
        atol=max(1e-15, float(np.median(steps)) * 1e-9),
    ):
        raise ValueError("校正波形时间轴必须严格递增且等间隔")
    if not np.isfinite(amplitude) or amplitude <= 0.0:
        raise ValueError("校正波形 amplitude_vpp 必须为正有限值")
    if not np.isfinite(offset) or not np.isfinite(repeat_frequency) or repeat_frequency <= 0.0:
        raise ValueError("校正波形 offset/repeat_frequency 无效")
    if np.max(np.abs(normalized)) > 1.0 + 1e-9:
        raise ValueError("校正波形归一化值超出 [-1, 1]")
    expected_voltage = offset + 0.5 * amplitude * normalized
    if not np.allclose(voltage, expected_voltage, rtol=1e-8, atol=1e-10):
        raise ValueError("校正波形电压与 normalized/Vpp/offset 不一致")
    expected_frequency = 1.0 / (time_s.size * float(np.median(steps)))
    if not np.isclose(repeat_frequency, expected_frequency, rtol=1e-6):
        raise ValueError("校正波形重复频率与时间轴不一致")
    return CorrectedControlWaveform(
        run_name=str(run_name),
        waveform_path=path,
        time_s=time_s,
        omega_ctrl_hz=omega,
        target_current_a=target_current,
        normalized=normalized,
        voltage_v=voltage,
        repeat_frequency_hz=repeat_frequency,
        amplitude_vpp=amplitude,
        offset_v=offset,
        waveform_sha256=sha256_file(path),
        coupling_calibration_run=coupling_run,
        coupling_calibration_sha256=coupling_sha256,
        frequency_response_run=response_run,
        frequency_response_sha256=response_sha256,
        error_cutoff_hz=error_cutoff_hz,
        inverse_regularization=inverse_regularization,
    )


def spectral_nrmse(target: Any, measured: Any) -> float:
    """返回去直流后单边幅度谱的归一化均方根误差。"""
    target_values = np.asarray(target, dtype=float).reshape(-1)
    measured_values = np.asarray(measured, dtype=float).reshape(-1)
    if target_values.size != measured_values.size or target_values.size < 2:
        raise ValueError("频谱误差输入长度不足或不一致")
    target_spectrum = np.abs(np.fft.rfft(target_values - np.mean(target_values)))
    measured_spectrum = np.abs(np.fft.rfft(measured_values - np.mean(measured_values)))
    denominator = float(np.sqrt(np.mean(target_spectrum * target_spectrum)))
    if denominator <= 0.0:
        raise ValueError("目标波形没有可比较的交流频谱")
    return float(
        np.sqrt(np.mean((measured_spectrum - target_spectrum) ** 2)) / denominator
    )


def relative_regularization_scale(
    transfer_function: Any,
    reliable_bins: Any,
    regularization: float,
) -> float:
    """把无量纲正则化比例换算为与传递函数同单位的绝对尺度。"""
    if not np.isfinite(regularization) or not 0.0 < regularization <= 1.0:
        raise ValueError("INVERSE_REGULARIZATION 必须在 (0, 1] 内")
    transfer = np.asarray(transfer_function, dtype=complex).reshape(-1)
    reliable = np.asarray(reliable_bins, dtype=bool).reshape(-1)
    if transfer.size == 0 or transfer.size != reliable.size:
        raise ValueError("传递函数与可靠频点掩码长度不一致")
    reliable_gain = np.abs(transfer[reliable])
    reliable_gain = reliable_gain[np.isfinite(reliable_gain) & (reliable_gain > 0.0)]
    if reliable_gain.size == 0:
        raise ValueError("可靠频点内没有正有限传递增益")
    return float(regularization) * float(np.max(reliable_gain))


def regularized_inverse_update(
    command: Any,
    target: Any,
    measured: Any,
    transfer_function: Any,
    *,
    damping: float,
    regularization: float,
    reliable_bins: Any,
) -> np.ndarray:
    """使用正则化逆传递函数生成一次阻尼迭代更新。"""
    command_values = np.asarray(command, dtype=float).reshape(-1)
    target_values = np.asarray(target, dtype=float).reshape(-1)
    measured_values = np.asarray(measured, dtype=float).reshape(-1)
    if not (
        command_values.size == target_values.size == measured_values.size
        and command_values.size >= 2
    ):
        raise ValueError("闭环更新输入数组长度不足或不一致")
    if not np.isfinite(damping) or not 0.0 < damping <= 1.0:
        raise ValueError("ITERATION_DAMPING 必须在 (0, 1] 内")
    error_spectrum = np.fft.rfft(target_values - measured_values)
    transfer = np.asarray(transfer_function, dtype=complex).reshape(-1)
    reliable = np.asarray(reliable_bins, dtype=bool).reshape(-1)
    if transfer.size != error_spectrum.size or reliable.size != error_spectrum.size:
        raise ValueError("传递函数、可靠频点掩码与波形 FFT 长度不一致")
    absolute_regularization = relative_regularization_scale(
        transfer,
        reliable,
        regularization,
    )
    inverse = np.conj(transfer) / (
        np.abs(transfer) ** 2 + absolute_regularization**2
    )
    correction_spectrum = np.where(reliable, inverse * error_spectrum, 0.0)
    correction = np.fft.irfft(correction_spectrum, n=command_values.size)
    return command_values + float(damping) * correction
