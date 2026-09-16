"""Z 线圈实际电流频率响应采集工作流：对数频率轴相干扫频。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...current_feedback import sense_voltage_to_current, validate_current_power
from ...experiment_runtime import load_runtime_params
from ..z_coil_inductance_frequency_response.workflow import run_frequency_response
from .models import ZCoilCurrentFrequencyResponseParams


EXPERIMENT_ID = "z-coil-current-frequency-response"
DATA_TYPE = "Z_Coil_Current_Frequency_Response"
EXECUTION_MODE = "typed_workflow"
OUTPUT_PROTOCOL = "coherent_swept_sine_current_response"
COMMAND_VOLTAGE_REFERENCE = "50_ohm"


def _validate_capture_current(
    frame: dict[str, Any], params: ZCoilCurrentFrequencyResponseParams,
) -> None:
    """按实测阻值检查每帧线圈电流与采样电阻功率。"""
    current_a = sense_voltage_to_current(
        frame["measured_voltage_v"], params.sense_resistor_ohm,
    )
    validate_current_power(
        current_a,
        params.sense_resistor_ohm,
        params.sense_resistor_power_rating_w,
        derating_fraction=params.sense_resistor_power_derating,
        maximum_current_a=params.maximum_current_a,
    )


def run(params: ZCoilCurrentFrequencyResponseParams) -> Path:
    """沿对数频率轴相干扫描 Z 线圈实际电流复响应。

    复用线圈端电压频响实验的成熟扫频框架：DG 内置正弦 + 外部下降沿无限
    Burst、每帧相干重触发、SDS 动态采样率与自动量程；CH3 改接低端采样
    电阻，电流由实测阻值换算。
    """
    return run_frequency_response(
        params,
        experiment_id=EXPERIMENT_ID,
        data_type=DATA_TYPE,
        acquisition_signals=[
            "SDS CH3 low-side sense-resistor voltage",
            "SDS CH4 common trigger reference",
        ],
        physical_limit=(
            "CH3 仅允许跨接低端采样电阻；电流由实测阻值换算，"
            "频响只在可靠频带内用于闭环逆滤波。"
        ),
        completion_label="Z 线圈实际电流频率响应",
        capture_validator=lambda frame: _validate_capture_current(frame, params),
        capture_guard_s=params.scope_capture_guard_s,
        drive_metadata={
            "output_protocol": OUTPUT_PROTOCOL,
            "command_voltage_reference": COMMAND_VOLTAGE_REFERENCE,
            "frequency_spacing": str(params.frequency_spacing),
            "frequency_points_per_decade": int(params.frequency_points_per_decade),
        },
    )


def main() -> int:
    run(load_runtime_params(ZCoilCurrentFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
