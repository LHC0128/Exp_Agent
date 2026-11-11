"""参数用途键元数据：只供目标接线图按器件只读展示，不连接仪器。"""

from __future__ import annotations

from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from .models import RFSensitivityDirectAWParams, RFSensitivityDirectAWSingleParams


def test_roles_reference_known_parameters_and_required_mapping_keys():
    # 单控制基类与多控制子类共用一张表；CONTROL_WAVEFORM_FILES 只在子类存在，基类 schema 不会命中它。
    assert RFSensitivityDirectAWParams.parameter_roles is RFSensitivityDirectAWSingleParams.parameter_roles
    names = set(RFSensitivityDirectAWParams.external_names())
    for name, roles in RFSensitivityDirectAWParams.parameter_roles.items():
        assert name in names, name
        assert roles and set(roles) <= set(RF_SENSITIVITY_DIRECT_AW), (name, roles)


def test_schema_exposes_roles_only_for_visible_linked_fields():
    fields = {field["name"]: field for field in RFSensitivityDirectAWParams().schema()["fields"]}
    assert fields["XY_TRIGGER_FREQ"]["roles"] == ["Time_sequence_2"]
    assert fields["Z_RF_FREQ"]["roles"] == ["Z_magnetic_field", "lockin_r"]
    assert fields["CONTROL_WAVEFORM_FILES"]["roles"] == ["X_magnetic_field", "Y_magnetic_field"]
    # 无仪器写入的参数不带 roles 键；隐藏字段不出现在 schema。
    assert "roles" not in fields["RUN_TAG"]
    assert "ARB_WAVEFORM_FILE" not in fields


GENERATOR_SETTING_FIELDS = {"frequency", "amplitude", "offset", "phase", "square_duty", "ramp_symmetry", "pulse_width", "pulse_delay"}


def test_settings_reference_known_parameters_roles_and_generator_fields():
    assert RFSensitivityDirectAWParams.parameter_settings is RFSensitivityDirectAWSingleParams.parameter_settings
    names = set(RFSensitivityDirectAWParams.external_names())
    for name, (role, field) in RFSensitivityDirectAWParams.parameter_settings.items():
        assert name in names, name
        assert role in RF_SENSITIVITY_DIRECT_AW, (name, role)
        assert role in RFSensitivityDirectAWParams.parameter_roles[name], (name, role)
        assert field in GENERATOR_SETTING_FIELDS, (name, field)


def test_settings_exclude_scanned_and_converted_parameters():
    excluded = {"RF_AMP_START", "RF_AMP_STOP", "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ", "Z_V_TO_NT", "PUMP_MOD_DUTY", "DEMOD0_TC_MEAS"}
    assert not excluded & set(RFSensitivityDirectAWParams.parameter_settings)


def test_schema_exposes_setting_only_for_bound_fields():
    fields = {field["name"]: field for field in RFSensitivityDirectAWParams().schema()["fields"]}
    assert fields["XY_TRIGGER_DUTY"]["setting"] == {"role": "Time_sequence_2", "field": "square_duty"}
    assert fields["PUMP_MOD_AMPLITUDE"]["setting"] == {"role": "Pump_modulation", "field": "amplitude"}
    assert fields["Z_RF_FREQ"]["setting"] == {"role": "Z_magnetic_field", "field": "frequency"}
    bound = set(RFSensitivityDirectAWParams.parameter_settings)
    for name, field in fields.items():
        assert ("setting" in field) == (name in bound), name
