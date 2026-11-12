"""参数用途键与设置项绑定元数据：供目标接线图展示与回存，用假信号源核对，不连接仪器。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from lab_workflows import control_sources
from lab_workflows.common import find_project_root
from ...experiments.requirements import RF_SENSITIVITY_DIRECT_AW
from . import excitation
from .models import RFSensitivityDirectAWParams, RFSensitivityDirectAWSingleParams
from .waveform import load_envelope


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


GENERATOR_SETTING_FIELDS = {"frequency", "amplitude", "offset", "phase", "square_duty", "ramp_symmetry",
                            "pulse_width", "pulse_delay", "burst_phase"}


def _bindings(relation: str):
    return [(name, binding)
            for name, bindings in RFSensitivityDirectAWParams.parameter_settings.items()
            for binding in bindings if binding.relation == relation]


def test_settings_reference_known_parameters_roles_and_generator_fields():
    assert RFSensitivityDirectAWParams.parameter_settings is RFSensitivityDirectAWSingleParams.parameter_settings
    names = set(RFSensitivityDirectAWParams.external_names())
    for name, bindings in RFSensitivityDirectAWParams.parameter_settings.items():
        assert name in names, name
        assert bindings, name
        for binding in bindings:
            assert binding.role in RF_SENSITIVITY_DIRECT_AW, (name, binding)
            assert binding.role in RFSensitivityDirectAWParams.parameter_roles[name], (name, binding)
            assert binding.field in GENERATOR_SETTING_FIELDS, (name, binding)


def test_scanned_and_offline_parameters_have_no_direct_binding():
    bound = RFSensitivityDirectAWParams.parameter_settings
    # 扫描量与标定换算量不等于单一仪器设置值，只按 derived 展示关联，不比较也不回存。
    for name in ("RF_AMP_START", "RF_AMP_STOP", "Z_V_TO_NT", "PUMP_MOD_DUTY",
                 "CONTROL_SCALE_FACTORS", "XY_CTRL_K_HZ_PER_V", "XY_CTRL_B_HZ"):
        assert all(binding.relation == "derived" for binding in bound.get(name, ())), name
    # 锁相、扫描点数与运行标签没有信号源设置项，完全不登记。
    for name in ("DEMOD0_TC_MEAS", "RF_AMP_POINTS", "Z_PHASE_SCAN_START_DEG", "RUN_TAG"):
        assert name not in bound, name


def test_schema_exposes_settings_and_first_direct_as_compatible_setting():
    fields = {field["name"]: field for field in RFSensitivityDirectAWParams().schema()["fields"]}
    assert fields["XY_TRIGGER_DUTY"]["settings"] == [
        {"role": "Time_sequence_2", "field": "square_duty", "relation": "direct"}]
    assert fields["PUMP_MOD_AMPLITUDE"]["setting"] == {"role": "Pump_modulation", "field": "amplitude"}
    assert fields["Z_RF_FREQ"]["setting"] == {"role": "Z_magnetic_field", "field": "frequency"}
    # 一个参数可写多个物理量：AW 重复频率同时写 X 与 Y。
    assert [(item["role"], item["field"], item["relation"])
            for item in fields["AW_REPEAT_FREQ"]["settings"]] == [
        ("X_magnetic_field", "frequency", "direct"), ("Y_magnetic_field", "frequency", "direct")]
    # derived 项带换算依据，且没有可回存的兼容 setting。
    duty = fields["PUMP_MOD_DUTY"]["settings"]
    assert [item["relation"] for item in duty] == ["derived"] * len(duty)
    assert all(item["note"] for item in duty)
    assert "setting" not in fields["PUMP_MOD_DUTY"]

    bound = RFSensitivityDirectAWParams.parameter_settings
    for name, field in fields.items():
        assert ("settings" in field) == (name in bound), name
        direct = [binding for binding in bound.get(name, ()) if binding.relation == "direct"]
        assert ("setting" in field) == bool(direct), name
        if direct:
            assert field["setting"] == {"role": direct[0].role, "field": direct[0].field}, name
        if name in bound:
            assert len(field["settings"]) == len(bound[name]), name


# 与 preparation.connect_devices 一致的路由名 → 用途键；通道号只在本测试内自洽。
CHANNEL_ROUTES = {"x": "X_magnetic_field", "y": "Y_magnetic_field", "z": "Z_magnetic_field",
                  "pump_rf": "Pump_modulation", "pump_gate": "Time_sequence", "trigger": "Time_sequence_2"}


class RecordingSignal:
    """按通道记录 setup_* / set_* 的假信号源，记录键与通道回读字段同名。"""

    ALIASES = {"freq": "frequency", "dcycle": "square_duty", "width": "pulse_width"}

    def __init__(self):
        self.channels: dict[int, dict[str, object]] = {}

    def __getattr__(self, name):
        def command(*args, channel=1, **kwargs):
            record = self.channels.setdefault(channel, {})
            if name.startswith("setup_"):
                for key, value in kwargs.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        record[self.ALIASES.get(key, key)] = value
            elif name.startswith("set_") and args:
                record[name[4:]] = args[0]
        return command


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    """用真实控制包络跑一遍激励配置，返回参数、按用途键记录的通道设置与合成波形。"""
    waveforms = tmp_path / "oc_sens/v1/waveforms"
    waveforms.mkdir(parents=True)
    (waveforms / "optimal_control_waveform.csv").write_bytes(
        (find_project_root() / "experiments/control_waveform.csv").read_bytes())
    monkeypatch.setitem(control_sources.CONTROL_SOURCE_SET_ROOTS, "oc_sens", tmp_path / "oc_sens")
    params = replace(RFSensitivityDirectAWParams(), arb_waveform_file="v1/waveforms/optimal_control_waveform.csv")
    signals = RecordingSignal()
    channels = {name: number for number, name in enumerate(CHANNEL_ROUTES, start=1)}
    ctx = SimpleNamespace(params=params, devices={"signals": signals, "channels": channels, "hf2": None},
                          state={}, directory=tmp_path)
    excitation.configure_trigger(ctx)
    excitation.configure_pump(ctx)
    excitation.prepare_z_rf(ctx)
    synthesized = excitation.upload_xy(signals, channels, params, load_envelope(params, 16384), params.xy_ctrl_phase)
    return params, {role: signals.channels[channels[name]] for name, role in CHANNEL_ROUTES.items()}, synthesized


def test_direct_bindings_equal_recorded_generator_writes(recorded):
    params, channels, _ = recorded
    values = params.to_external()
    direct = _bindings("direct")
    assert direct
    for name, binding in direct:
        # 容差与调试页 differs() 和 load_envelope 的公共周期检查一致。
        assert channels[binding.role][binding.field] == pytest.approx(values[name], rel=1e-6, abs=1e-9), (name, binding)


def test_derived_bindings_document_conversion_instead_of_parameter_value(recorded):
    params, channels, synthesized = recorded
    values = params.to_external()
    derived = _bindings("derived")
    assert derived
    for name, binding in derived:
        assert binding.note, (name, binding)
        written = channels[binding.role][binding.field]
        if isinstance(values[name], (int, float)):
            assert written != pytest.approx(values[name], rel=1e-6, abs=1e-9), (name, binding)
    # note 中的换算依据与实际写入一致。
    assert channels["Time_sequence"]["pulse_width"] == pytest.approx(params.pump_mod_duty / 100 / params.pump_mod_freq)
    peak_v = float(abs(synthesized["envelope_V"]).max())
    for role in ("X_magnetic_field", "Y_magnetic_field"):
        assert channels[role]["amplitude"] == pytest.approx(2 * peak_v)
        assert channels[role]["offset"] == 0.0