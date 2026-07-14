"""Demod0 安全相位校准。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from lockin_amplifier import HF2Instrument
from signal_generator import DG4000Instrument, DG900Instrument

from .common import ProgressCallback, emit, load_mapping
from .steps import (
    DeviceSession,
    PhaseCalibrationConfig,
    calibrate_demod_phase,
    phase_calibration_guard,
    set_temperature_switch,
)


@dataclass(slots=True)
class PhaseCalibrationResult:
    before_sample: dict[str, float]
    after_sample: dict[str, float]
    phase_shift_deg: float
    restored: bool
    restore_errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibrate_demod0_safely(
    tolerance_deg: float = 1.0,
    max_attempts: int = 5,
    settle_time: float = 0.2,
    progress: ProgressCallback | None = None,
) -> PhaseCalibrationResult:
    """暂时关闭 Z 场与温控开关，校准后恢复其原始状态。"""
    mapping = load_mapping()
    z_cfg = mapping["Z_magnetic_field"]
    temp_cfg = mapping["Temp_Switch"]
    hf_cfg = mapping["lockin_r"]
    z = DG4000Instrument(z_cfg["resource"], channel=int(z_cfg["channel"]))
    temp = DG900Instrument(temp_cfg["resource"], channel=int(temp_cfg["channel"]))
    hf2 = HF2Instrument(
        host=hf_cfg.get("host", "127.0.0.1"),
        port=hf_cfg.get("port", 8005),
        api_level=1,
        device_id=hf_cfg["device_id"],
    )
    session = DeviceSession()
    restore_errors: list[str] = []
    shared_result = None

    try:
        session.connect("z_field", z_cfg["resource"], lambda: z)
        emit(progress, "connect", "Z 场已连接")
        session.connect("temperature_switch", temp_cfg["resource"], lambda: temp)
        emit(progress, "connect", "温控开关已连接")
        session.connect(
            "hf2",
            f"hf2://{hf_cfg.get('host', '127.0.0.1')}/{hf_cfg['device_id']}",
            lambda: hf2,
        )
        emit(progress, "connect", "HF2 已连接")

        z_channel = int(z_cfg["channel"])
        temp_channel = int(temp_cfg["channel"])

        def prepare_z():
            output = z.get_output(channel=z_channel)
            z.set_output(False, channel=z_channel)
            return lambda: z.set_output(output, channel=z_channel)

        def prepare_temperature():
            output = temp.get_output(channel=temp_channel)
            voltage = temp.get_dc_voltage(channel=temp_channel)
            set_temperature_switch(
                temp, False, channel=temp_channel, settle_time=settle_time
            )

            def restore() -> None:
                temp.setup_dc(voltage, channel=temp_channel)
                temp.set_output(output, channel=temp_channel)

            return restore

        with phase_calibration_guard([
            ("Z 场恢复失败", prepare_z),
            ("温控开关恢复失败", prepare_temperature),
        ]) as guard:
            emit(progress, "prepare", "已临时关闭 Z 场和温控干扰", 25)
            shared_result = calibrate_demod_phase(
                hf2,
                PhaseCalibrationConfig(
                    demod_idx=0,
                    tolerance_deg=tolerance_deg,
                    max_attempts=max_attempts,
                    settle_time=settle_time,
                ),
                progress=progress,
            )
            emit(progress, "calibrate", "Demod0 相位校准完成", 80)
        restore_errors.extend(guard.restore_errors)
    finally:
        session.cleanup_connection_failure()
        emit(
            progress,
            "restore",
            "实验环境已恢复" if not restore_errors else "部分状态恢复失败",
            100,
            "info" if not restore_errors else "error",
        )

    if shared_result is None:
        raise RuntimeError("相位校准未返回结果")
    return PhaseCalibrationResult(
        before_sample=shared_result.before_sample,
        after_sample=shared_result.after_sample,
        phase_shift_deg=shared_result.phase_shift_deg,
        restored=not restore_errors,
        restore_errors=restore_errors,
    )
