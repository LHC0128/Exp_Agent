"""公共阶段类型、初始状态及旧阶段入口的微型契约。"""

from dataclasses import FrozenInstanceError, asdict, fields, replace
import importlib
import json

import pytest

from .workflow_stages import DependencyRules, Stage, initial_states
from .workflow_state import read_state, write_state


FIELD_NAMES = ["id", "title", "group", "dependencies", "parameters", "artifacts",
               "display_parameters", "quality_gates"]


def test_stage_fields_defaults_frozen_and_json_contract():
    stage = Stage("B01", "读取数据", "离线分析", ("A01",), ("GAIN",), ("result.json",),
                  ("LABEL",), ("finite",))
    expected = dict(id="B01", title="读取数据", group="离线分析", dependencies=("A01",),
                    parameters=("GAIN",), artifacts=("result.json",),
                    display_parameters=("LABEL",), quality_gates=("finite",))
    assert [field.name for field in fields(Stage)] == FIELD_NAMES
    assert asdict(stage) == expected
    assert json.loads(json.dumps(asdict(stage), ensure_ascii=False, allow_nan=False)) == dict(
        id="B01", title="读取数据", group="离线分析", dependencies=["A01"], parameters=["GAIN"],
        artifacts=["result.json"], display_parameters=["LABEL"], quality_gates=["finite"])
    default = Stage("A01", "预检", "准备", ())
    assert asdict(default) == dict(id="A01", title="预检", group="准备", dependencies=(),
                                  parameters=(), artifacts=(), display_parameters=None, quality_gates=())
    with pytest.raises(FrozenInstanceError):
        stage.title = "禁止原地修改"
    changed = replace(stage, title="独立替换", display_parameters=())
    assert stage.title == "读取数据" and changed.title == "独立替换"
    assert changed.display_parameters == () and default.display_parameters is None
    assert stage == Stage(**expected) and hash(stage) == hash(Stage(**expected))


def test_initial_states_order_isolation_and_file_round_trip(tmp_path):
    stages = (Stage("B02", "后处理", "分析", ("B01",)), Stage("B01", "读取", "分析", ()))
    expected = {"B02": {"status": "pending", "error": ""}, "B01": {"status": "pending", "error": ""}}
    states = initial_states(iter(stages))
    assert states == expected and list(states) == ["B02", "B01"]
    assert initial_states(()) == {}
    states["B02"].update(status="failed", error="示例错误")
    assert states["B01"] == {"status": "pending", "error": ""}
    assert initial_states(stages) == expected
    path = tmp_path / "阶段状态.json"
    write_state(path, states)
    assert read_state(path) == states


@pytest.mark.parametrize("name", [
    "noise_spectrum_xy", "detection_chain_frequency_response", "mx_y_rf_sensitivity",
    "t2_calibration", "rf_sensitivity_direct_aw_frequency", "xy_direct_aw_dc_calibration",
])
def test_old_stage_imports_share_public_symbols(name):
    module = importlib.import_module(f"lab_workflows.experiment_modules.{name}.stages")
    assert module.Stage is Stage and module.initial_states is initial_states
    assert all(type(stage) is Stage for stage in module.ACQUISITION + module.ANALYSIS)
    assert module.initial_states(module.ANALYSIS) == initial_states(module.ANALYSIS)
    # 真实实验阶段仍按原字段序列化，不引入类型标签或新状态字段。
    assert list(asdict(module.ACQUISITION[0])) == FIELD_NAMES
    assert json.loads(json.dumps(module.definition(), ensure_ascii=False, allow_nan=False))


@pytest.mark.parametrize("name,finish_id,temperature_impacts", [
    ("noise_spectrum_xy", "A14", ["A05", "A06", "A07", "A08", "A09", "A10", "A11", "A12", "A13"]),
    ("detection_chain_frequency_response", "A08", ["A04", "A05", "A06", "A07"]),
])
def test_experiment_rules_keep_their_own_finish_and_parameter_impacts(name, finish_id, temperature_impacts):
    module = importlib.import_module(f"lab_workflows.experiment_modules.{name}.stages")
    stages = module.ACQUISITION
    states = initial_states(stages)
    for state in states.values():
        state["status"] = "completed"
    module.invalidate(stages, states, "A01")
    assert states[finish_id] == {"status": "completed", "error": ""}
    assert {key for key, value in states.items() if value["status"] == "stale"} == {
        stage.id for stage in stages if stage.id != finish_id}
    with pytest.raises(ValueError, match="安全收尾必须最后执行"):
        module.validate_selection(stages, states, [finish_id, "A01"])
    assert module.validate_selection(stages, states, [finish_id]) == [finish_id]
    assert module.definition()["parameter_impacts"]["acquisition"]["FIXED_PARAMS.temperature"] == temperature_impacts


@pytest.mark.parametrize("finish_id", ["A07", "A08", "A10", "A12"])
def test_dependency_rules_keep_explicit_finish_and_only_invalidate_dependants(finish_id):
    stages = (
        Stage("A01", "预检", "准备", ()),
        Stage("A02", "配置", "采集", ("A01",), ("GAIN",)),
        Stage("A03", "测量", "采集", ("A02",), ("GAIN",)),
        Stage("B01", "独立分支", "分析", (), ("PLOT",)),
        Stage(finish_id, "安全结束", "结束", ()),
    )
    rules = DependencyRules(finish_id=finish_id)
    states = initial_states(stages)
    states["A02"].update(status="completed", error="旧错误")
    states["A03"]["error"] = "旧错误"
    states["B01"].update(status="quality_failed", error="独立错误")
    states[finish_id]["status"] = "completed"
    assert [stage.id for stage in rules.invalidated_stages(stages, "A02")] == ["A02", "A03"]
    rules.invalidate(stages, states, "A02")
    assert states["A02"] == {"status": "stale", "error": ""}
    assert states["A03"] == {"status": "pending", "error": ""}
    assert states["B01"] == {"status": "quality_failed", "error": "独立错误"}
    rules.invalidate(stages, states, finish_id)
    assert states[finish_id] == {"status": "completed", "error": ""}
    assert rules.parameter_impacts(stages) == {"GAIN": ["A02", "A03"], "PLOT": ["B01"]}
    with pytest.raises(FrozenInstanceError):
        rules.finish_id = "别的实验的结束节点"


def test_analysis_rules_have_no_implicit_finish_node():
    stages = (
        Stage("B01", "读取", "分析", (), ("FILTER",)),
        Stage("B02", "计算", "分析", ("B01",)),
    )
    rules = DependencyRules(finish_id=None)
    states = {stage.id: {"status": "completed", "error": ""} for stage in stages}
    assert rules.parameter_impacts(stages) == {"FILTER": ["B01", "B02"]}
    assert rules.validate_selection(stages, states, ["B02", "B01"]) == ["B01", "B02"]
    rules.invalidate(stages, states, "B01")
    assert states == {stage.id: {"status": "stale", "error": ""} for stage in stages}


def test_dependency_selection_preserves_preflight_exception_and_rerun_invalidation():
    stages = (
        Stage("A01", "预检", "准备", ()),
        Stage("A02", "配置", "准备", ("A01",)),
        Stage("A03", "测量", "采集", ("A02",)),
        Stage("A04", "保存", "采集", ("A03",)),
        Stage("CLOSE", "结束", "结束", ()),
    )
    rules = DependencyRules(finish_id="CLOSE", finish_error="自定义结束提示")
    pending = initial_states(stages)
    assert rules.validate_selection(stages, pending, ["CLOSE"]) == ["CLOSE"]
    assert rules.validate_selection(stages, pending, ["A03", "A01", "A02", "CLOSE"]) == [
        "A01", "A02", "A03", "CLOSE"]
    for selected in ([], ["不存在"], ["A01", "A01"]):
        with pytest.raises(ValueError, match="有效且不重复"):
            rules.validate_selection(stages, pending, selected)
    with pytest.raises(ValueError, match="自定义结束提示"):
        rules.validate_selection(stages, pending, ["CLOSE", "A01"])
    completed = {stage.id: {"status": "completed", "error": ""} for stage in stages}
    assert rules.validate_selection(stages, completed, ["A01", "A04"]) == ["A01", "A04"]
    with pytest.raises(ValueError, match="A04 保存 缺少前置模块：A03 测量"):
        rules.validate_selection(stages, completed, ["A02", "A04"])
    assert all(state["status"] == "completed" for state in completed.values())


@pytest.mark.parametrize("name", ["mx_y_rf_sensitivity", "bb_duty_scan_burst", "t1_calibration"])
def test_migrated_stage_entrypoints_bind_their_own_finish_without_peer_state(name):
    module = importlib.import_module(f"lab_workflows.experiment_modules.{name}.stages")
    states = initial_states(module.ACQUISITION)
    states[module.FINISH_ID]["status"] = "completed"
    module.invalidate(module.ACQUISITION, states, module.FINISH_ID)
    assert states[module.FINISH_ID] == {"status": "completed", "error": ""}
    assert module.validate_selection(module.ACQUISITION, states, [module.FINISH_ID]) == [module.FINISH_ID]
    assert module.definition()["finish_step_id"] == module.FINISH_ID
