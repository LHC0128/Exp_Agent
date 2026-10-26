"""迁移阶段入口的本地结束节点、公开导出和特殊校验契约；不连接仪器。"""

from copy import deepcopy
import importlib
import inspect
import json

import pytest

from .workflow_stages import Stage, initial_states


MIGRATED_MODULES = (
    "bb_power_scan_burst",
    "mx_main_field_scope_noise_spectrum",
    "mx_y_optimal_control_rf_frequency_response",
    "mx_xy_residual_field_calibration",
    "mx_y_rf_probe_detuning_optimization",
    "mx_z_optimal_control_rf_sensitivity",
    "noise_spectrum_xy_demod3_r",
    "photon_shot_noise",
    "projection_noise",
    "rf_sensitivity_constxy_frequency",
    "rf_sensitivity_direct_aw_frequency",
    "t2_calibration",
    "xy_direct_aw_dc_calibration",
    "z_field_bandwidth",
)
DIRECT_VALIDATORS = {
    "mx_y_optimal_control_rf_frequency_response", "noise_spectrum_xy_demod3_r",
}
SPECIAL_VALIDATOR = "mx_z_optimal_control_rf_sensitivity"


def _module(name):
    return importlib.import_module(f"lab_workflows.experiment_modules.{name}.stages")


def _small_graph(finish_id):
    return (
        Stage("A01", "预检", "准备", ()),
        Stage("A02", "配置", "准备", ("A01",), ("GAIN",)),
        Stage("A03", "测量", "采集", ("A02",), ("GAIN",)),
        Stage("B01", "独立分支", "分析", (), ("PLOT",)),
        Stage(finish_id, "结束", "结束", ()),
    )


@pytest.mark.parametrize("name", MIGRATED_MODULES)
def test_migrated_stage_rule_bindings(name):
    module = _module(name)
    assert module.Stage is Stage and module.initial_states is initial_states
    expected_parameters = {
        "invalidated_stages": ["stages", "stage_id"],
        "invalidate": ["stages", "states", "stage_id"],
        "parameter_impacts": ["stages"],
        "validate_selection": ["stages", "states", "selected"],
    }
    for method, parameters in expected_parameters.items():
        assert list(inspect.signature(getattr(module, method)).parameters) == parameters

    stages = _small_graph(module.FINISH_ID)
    completed = {stage.id: {"status": "completed", "error": ""} for stage in stages}
    states = deepcopy(completed)
    module.invalidate(stages, states, module.FINISH_ID)
    assert states == completed
    assert module.invalidated_stages(stages, module.FINISH_ID) == ()
    module.invalidate(stages, states, "A02")
    assert states["A02"] == states["A03"] == {"status": "stale", "error": ""}
    assert states[module.FINISH_ID] == states["B01"] == {"status": "completed", "error": ""}
    assert module.parameter_impacts(stages) == {"GAIN": ["A02", "A03"], "PLOT": ["B01"]}

    # 本地结束节点独立于捐赠实验 A12；A12 在另一个图中仍能是普通节点。
    if module.FINISH_ID != "A12":
        with_a12 = (*stages[:3], Stage("A12", "普通后继", "采集", ("A03",)), *stages[3:])
        assert [stage.id for stage in module.invalidated_stages(with_a12, "A02")] == [
            "A02", "A03", "A12",
        ]

    pending = initial_states(stages)
    before = deepcopy(pending)
    assert module.validate_selection(stages, pending, [module.FINISH_ID]) == [module.FINISH_ID]
    assert module.validate_selection(stages, pending, ["A03", "A01", "A02", module.FINISH_ID]) == [
        "A01", "A02", "A03", module.FINISH_ID,
    ]
    suffix = "A02" if name == SPECIAL_VALIDATOR else "A02 配置"
    with pytest.raises(ValueError) as missing:
        module.validate_selection(stages, pending, ["A03"])
    assert str(missing.value) == "A03 测量 缺少前置模块：" + suffix
    for selected in ([], ["不存在"], ["A01", "A01"]):
        with pytest.raises(ValueError, match="^请选择有效且不重复的模块$"):
            module.validate_selection(stages, pending, selected)
    assert pending == before

    definition = json.loads(json.dumps(module.definition(), ensure_ascii=False, allow_nan=False))
    assert definition["finish_step_id"] == module.FINISH_ID
    assert definition["run_step_id"] in {stage["id"] for stage in definition["acquisition"]}
    assert all(type(stage) is Stage for stage in module.ACQUISITION + module.ANALYSIS)
    assert definition["parameter_impacts"] == {
        "acquisition": module.parameter_impacts(module.ACQUISITION),
        "analysis": module.parameter_impacts(module.ANALYSIS),
    }


def test_local_finish_wrappers_keep_error_text_and_validation_order():
    for name in MIGRATED_MODULES:
        module = _module(name)
        stages = _small_graph(module.FINISH_ID)
        states = initial_states(stages)
        # 已有 wrapper 先校验结束位置，通用入口与特殊 MxZ 校验先检查未知编号。
        expected = "请选择有效且不重复的模块" if name in DIRECT_VALIDATORS | {SPECIAL_VALIDATOR} else (
            "结束会话必须最后执行" if name == "bb_power_scan_burst" else "安全结束必须最后执行"
        )
        with pytest.raises(ValueError) as invalid:
            module.validate_selection(stages, states, [module.FINISH_ID, "不存在"])
        assert str(invalid.value) == expected, name
        expected_finish = "结束会话必须最后执行" if name == "bb_power_scan_burst" else "安全结束必须最后执行"
        with pytest.raises(ValueError) as finish:
            module.validate_selection(stages, states, [module.FINISH_ID, "A01"])
        assert str(finish.value) == expected_finish, name


def test_mx_z_preserves_preflight_invalidation_and_short_missing_message():
    module = _module(SPECIAL_VALIDATOR)
    stages = _small_graph(module.FINISH_ID)
    states = {stage.id: {"status": "completed", "error": ""} for stage in stages}
    before = deepcopy(states)
    with pytest.raises(ValueError) as missing:
        module.validate_selection(stages, states, ["A01", "A03"])
    assert str(missing.value) == "A03 测量 缺少前置模块：A02"
    assert module.validate_selection(stages, states, ["A03", "A02", "A01"]) == ["A01", "A02", "A03"]
    assert module.validate_selection(stages, states, [module.FINISH_ID]) == [module.FINISH_ID]
    assert states == before
