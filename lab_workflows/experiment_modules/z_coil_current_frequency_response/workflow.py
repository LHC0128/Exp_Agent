"""Z 线圈实际电流频率响应采集工作流。"""

from __future__ import annotations

from pathlib import Path

from ...current_feedback import sense_voltage_to_current, validate_current_power
from ...experiment_runtime import load_runtime_params
from ..z_coil_inductance_frequency_response.workflow import run_frequency_response
from .models import ZCoilCurrentFrequencyResponseParams


EXPERIMENT_ID = "z-coil-current-frequency-response"
DATA_TYPE = "Z_Coil_Current_Frequency_Response"
EXECUTION_MODE = "typed_workflow"


def run(params: ZCoilCurrentFrequencyResponseParams) -> Path:
    """复用相干正弦扫频，CH3 物理量解释为采样电阻电压。"""
    def validate_capture(frame: dict[str, object]) -> None:
        current_a = sense_voltage_to_current(
            frame["measured_voltage_v"],
            params.sense_resistor_ohm,
        )
        validate_current_power(
            current_a,
            params.sense_resistor_ohm,
            params.sense_resistor_power_rating_w,
            derating_fraction=params.sense_resistor_power_derating,
            maximum_current_a=params.maximum_current_a,
        )

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
            "频响只在可靠带宽内用于闭环逆滤波。"
        ),
        completion_label="Z 线圈实际电流频率响应",
        capture_validator=validate_capture,
        capture_guard_s=params.scope_capture_guard_s,
    )


def main() -> int:
    run(load_runtime_params(ZCoilCurrentFrequencyResponseParams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
