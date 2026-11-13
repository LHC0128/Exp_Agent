"""DirectAW 家族运行目录的初始元数据；三实验共用同一键集与初始值。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def initial_state(params, identity) -> dict[str, Any]:
    """运行目录初始配置。

    identity 为实验的运行格式模块，提供 EXPERIMENT_ID、ACQUISITION_METHOD 与
    RESPONSE_COMPONENT；键集与历史运行保持一致，分析侧按这些键判断资格。
    """
    return {
        "experiment_id": identity.EXPERIMENT_ID, "execution_mode": "typed_workflow",
        "acquisition_method": identity.ACQUISITION_METHOD, "response_component": identity.RESPONSE_COMPONENT,
        "noise_component": "X", "started_at": datetime.now().isoformat(),
        "completion_status": "running", "analysis_status": "not_started",
        "phase_calibration_status": "not_started", "xy_phase_calibration": {"converged": False, "history": []},
        "rf_phase_calibration": {"points_completed": 0},
        "signal_chain": params.signal_chain(identity.RESPONSE_COMPONENT), "common_trigger": {"mapping_key": "Time_sequence_2", "output": "channel", "targets": ["X Ext Trig", "Y Ext Trig", "Z Ext Trig"], "frequency_Hz": params.xy_trigger_freq},
        "calibration": {"z_positive_field_phase_deg": params.z_burst_phase_init % 360, "z_negative_field_phase_deg": (params.z_burst_phase_init + 180) % 360, "z_v_to_nT": params.z_v_to_nt},
        "actual_rates": {}, "data_files": [], "response_points_completed": 0, "noise_records_completed": 0,
        "bandwidth_points_completed": 0,
    }
