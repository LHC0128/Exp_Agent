"""模型默认、版本化 YAML、GUI schema 与 CLI 默认值的一致性测试。

版本化 YAML（params/experiments/*.yaml）是唯一 source of truth：
GUI schema 与 CLI 都通过 ``TypedWorkflowAdapter.defaults()`` 读取它，
模型 dataclass 默认值作为 YAML 缺失时的回退，必须与 YAML 保持一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity import (
    ADAPTER as KEITHLEY_RF_ADAPTER,
    DEFINITION as KEITHLEY_RF_DEFINITION,
)
from lab_workflows.experiment_modules.mx_keithley_6221_optimal_control_rf_sensitivity.models import (
    MxKeithley6221OptimalControlRFParams,
)
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization import (
    ADAPTER as PROBE_ADAPTER,
    DEFINITION as PROBE_DEFINITION,
)
from lab_workflows.experiment_modules.mx_y_rf_probe_detuning_optimization.models import (
    MxYRFProbeDetuningOptimizationParams,
)
from lab_workflows.experiment_modules.mx_z_field_calibration import (
    ADAPTER as Z_CAL_ADAPTER,
    DEFINITION as Z_CAL_DEFINITION,
)
from lab_workflows.experiment_modules.mx_z_field_calibration.models import (
    MxZFieldCalibrationParams,
)

ROOT = Path(__file__).resolve().parents[2]


def _schema_default(definition, external_name: str):
    fields = {item["name"]: item for item in definition.schema()["fields"]}
    assert external_name in fields, f"schema 缺少字段 {external_name}"
    return fields[external_name]["default"]


@pytest.mark.parametrize(
    (
        "adapter",
        "definition",
        "params_type",
        "external_name",
        "expected",
    ),
    [
        (
            KEITHLEY_RF_ADAPTER,
            KEITHLEY_RF_DEFINITION,
            MxKeithley6221OptimalControlRFParams,
            "CONTROL_VERSION",
            "v4",
        ),
        (
            KEITHLEY_RF_ADAPTER,
            KEITHLEY_RF_DEFINITION,
            MxKeithley6221OptimalControlRFParams,
            "Y_RF_FREQUENCY_HZ",
            12000.0,
        ),
        (
            PROBE_ADAPTER,
            PROBE_DEFINITION,
            MxYRFProbeDetuningOptimizationParams,
            "PZT_VOLTAGE_START_V",
            20.0,
        ),
        (
            PROBE_ADAPTER,
            PROBE_DEFINITION,
            MxYRFProbeDetuningOptimizationParams,
            "PZT_VOLTAGE_STOP_V",
            120.0,
        ),
        (
            PROBE_ADAPTER,
            PROBE_DEFINITION,
            MxYRFProbeDetuningOptimizationParams,
            "PZT_VOLTAGE_POINTS",
            21,
        ),
        (
            Z_CAL_ADAPTER,
            Z_CAL_DEFINITION,
            MxZFieldCalibrationParams,
            "FIXED_PARAMS.main_magnetic_field",
            0.0,
        ),
    ],
)
def test_yaml_model_and_schema_defaults_agree(
    adapter,
    definition,
    params_type,
    external_name,
    expected,
) -> None:
    # GUI/CLI 生效默认（版本化 YAML）。
    yaml_default = adapter.defaults().to_external()[external_name]
    # 模型 dataclass 回退默认。
    model_default = params_type().to_external()[external_name]
    # GUI schema 展示的默认（同样来自 YAML）。
    schema_default = _schema_default(definition, external_name)
    assert yaml_default == pytest.approx(expected) if isinstance(
        expected, float
    ) else yaml_default == expected
    assert model_default == pytest.approx(expected) if isinstance(
        expected, float
    ) else model_default == expected
    assert schema_default == pytest.approx(expected) if isinstance(
        expected, float
    ) else schema_default == expected


def test_gui_schema_and_cli_share_the_same_defaults_source() -> None:
    """schema() 与 defaults() 都由 TypedWorkflowAdapter 从同一 YAML 生成。"""
    defaults = KEITHLEY_RF_ADAPTER.defaults().to_external()
    fields = {
        item["name"]: item for item in KEITHLEY_RF_DEFINITION.schema()["fields"]
    }
    for name, entry in fields.items():
        if name in defaults:
            assert entry["default"] == defaults[name]
