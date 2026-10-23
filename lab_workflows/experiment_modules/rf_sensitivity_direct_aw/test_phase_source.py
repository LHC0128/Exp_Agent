"""扫描与手动 RF 相位来源的离线资格回归，不连接仪器。"""
from copy import deepcopy
import pytest
from .run_format import manual_rf_phase


def manual_config(phase=181.5):
    return {
        "parameters": {"Z_PHASE_SCAN_ENABLED": False, "Z_BURST_PHASE_INIT": phase},
        "rf_phase_calibration": {"mode": "manual", "scan_skipped": True,
            "points_completed": 0, "selected_phase_deg": phase % 360},
    }


@pytest.mark.parametrize("phase", [181.5, -25.0, 725.0])
def test_manual_phase_requires_explicit_consistent_provenance(phase):
    assert manual_rf_phase(manual_config(phase)) == phase % 360


@pytest.mark.parametrize("fault", ["missing", "scan_mode", "not_skipped", "points", "phase",
                                   "nonfinite", "bool_phase", "wrong_switch"])
def test_incomplete_or_forged_manual_phase_is_rejected(fault):
    config = deepcopy(manual_config())
    record = config["rf_phase_calibration"]
    if fault == "missing":
        config.pop("rf_phase_calibration")
    elif fault == "scan_mode":
        record["mode"] = "scan"
    elif fault == "not_skipped":
        record["scan_skipped"] = False
    elif fault == "points":
        record["points_completed"] = 1
    elif fault == "phase":
        record["selected_phase_deg"] += 1
    elif fault == "nonfinite":
        record["selected_phase_deg"] = float("nan")
    elif fault == "bool_phase":
        config["parameters"]["Z_BURST_PHASE_INIT"] = True
    else:
        config["parameters"]["Z_PHASE_SCAN_ENABLED"] = "false"
    with pytest.raises(ValueError, match="RF 相位"):
        manual_rf_phase(config)


def test_old_missing_switch_stays_scan_and_does_not_accept_manual_provenance():
    assert manual_rf_phase({"parameters": {}}) is None
    config = manual_config()
    config["parameters"].pop("Z_PHASE_SCAN_ENABLED")
    with pytest.raises(ValueError, match="RF 相位"):
        manual_rf_phase(config)
