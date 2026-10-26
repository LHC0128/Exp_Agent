"""活动分步实验的清单与公共 API 契约；不创建硬件任务。"""
from dataclasses import fields
from importlib import import_module
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from .workflow_sessions import WorkflowSessionService, XYFlow, create_router
from pydantic import ValidationError

MODULES = tuple(sorted(path.parent.name for path in
    (Path(__file__).parents[2] / "lab_workflows/experiment_modules").glob("*/stages.py")))


@pytest.mark.parametrize("name", MODULES)
def test_explicit_roles_survive_real_workflow_api(name, tmp_path):
    module = import_module(f"GUI.backend.{name}")
    service_type, = (value for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__
        and issubclass(value, WorkflowSessionService))
    service = service_type(lambda: tmp_path, None)
    from lab_workflows.experiments import get_experiment
    registered = get_experiment(service.adapter.id)
    assert registered.data_type == service.adapter.data_type
    assert registered.execution_mode == "typed_workflow"
    session = import_module(service.session_module)
    assert callable(session.main)
    assert callable(session.Workflow.execute)
    assert callable(session.Workflow.finish)
    if service.analysis_workflow is not None:
        assert callable(import_module(service.adapter.analysis_module).main)
    declared = service.stages.definition()
    acquisition = {stage.id: stage for stage in service.stages.ACQUISITION}
    assert declared["run_step_id"] in acquisition
    assert declared["finish_step_id"] in acquisition
    assert declared["run_step_id"] != declared["finish_step_id"]
    assert acquisition[declared["finish_step_id"]].dependencies == ()
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as client:
        response = client.get(f"/api/experiments/{service.adapter.id}/workflow")
    assert response.status_code == 200
    assert XYFlow.model_validate(response.json()).model_dump(mode="json") == XYFlow.model_validate(declared).model_dump(mode="json")


@pytest.mark.parametrize("name", MODULES)
def test_model_editors_and_parameter_impacts_match_existing_invalidation(name, tmp_path):
    module = import_module(f"GUI.backend.{name}")
    service_type, = (value for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__
        and issubclass(value, WorkflowSessionService))
    service = service_type(lambda: tmp_path, None)
    model = {item.metadata["external_name"]: item for item in fields(service.adapter.params_type)}
    definition = service.stages.definition()
    editors = {key for stages in (definition["acquisition"], definition["analysis"])
        for stage in stages for key in stage["display_parameters"]}
    visible = {key for key, item in model.items() if item.metadata.get("visible", True)}
    assert visible <= editors
    assert editors <= model.keys()
    for column, stages in (("acquisition", service.stages.ACQUISITION), ("analysis", service.stages.ANALYSIS)):
        impacts = definition["parameter_impacts"][column]
        claimed = {key for stage in stages for key in stage.parameters}
        assert claimed <= model.keys()
        assert claimed == impacts.keys()
        for changed in [(key,) for key in claimed] + [tuple(claimed)]:
            states = {stage.id: dict(status="completed", error="") for stage in stages}
            for stage in stages:
                if set(changed).intersection(stage.parameters):
                    service.stages.invalidate(stages, states, stage.id)
            actual = {key for key, value in states.items() if value["status"] == "stale"}
            expected = {key for parameter in changed for key in impacts[parameter]}
            assert actual == expected, (column, changed, actual, expected)


@pytest.mark.parametrize("name", MODULES)
def test_real_parameter_application_merges_impacts_without_execution(name, tmp_path, monkeypatch):
    """只隔离预检/通知等边界；真正调用各工作流的 update_parameters。"""
    module = import_module(f"GUI.backend.{name}")
    service_type, = (value for value in vars(module).values()
        if isinstance(value, type) and value.__module__ == module.__name__
        and issubclass(value, WorkflowSessionService))
    service = service_type(lambda: tmp_path, None)
    workflow = import_module(f"lab_workflows.experiment_modules.{name}.workflow")
    ctx = workflow.Workflow.__new__(workflow.Workflow)
    ctx.params_type = service.adapter.params_type
    ctx.params = ctx.params_type()
    ctx.root, ctx.devices, ctx.current_step, ctx.revision = tmp_path, {}, None, 0
    ctx.recheck = ctx._check_configuration = lambda: None
    notices = []
    ctx.publish = lambda *args: notices.append(args)
    ctx.preflight = lambda params, *args: (params, {}, {}, {}) if name.startswith("noise_spectrum_xy") else (params, {}, {})
    # 实验的全量预检已有安全回归；这里不构造仪器或扫描，也不把此隔离当安全验证。
    for owner in type(ctx).__mro__:
        if owner is object:
            continue
        namespace = import_module(owner.__module__)
        if hasattr(namespace, "preflight"):
            monkeypatch.setattr(namespace, "preflight", lambda params, *args: (params, {}))
        if preparation := getattr(namespace, "preparation", None):
            monkeypatch.setattr(preparation, "preflight", lambda params, *args: (params, {}), raising=False)
            if hasattr(preparation, "source_revision"):
                monkeypatch.setattr(preparation, "source_revision", lambda *args: "same-source")
    if name in ("rf_sensitivity_direct_aw_frequency", "xy_direct_aw_dc_calibration"):
        monkeypatch.setattr(workflow.preparation, "settings", lambda ctx: None)
    stages = service.stages.ACQUISITION
    impacts = service.stages.definition()["parameter_impacts"]["acquisition"]
    before = ctx.params.to_external()
    tag = next(key for key in ("RUN_TAG", "run_tag") if key in impacts)
    other = next(key for key in impacts if key != tag and isinstance(before[key], (int, float))
        and not isinstance(before[key], bool))
    changes = {tag: "contract-draft", other: before[other] + 1}
    ctx.states = {stage.id: dict(status="completed", error="") for stage in stages}
    ctx.update_parameters(changes)
    changed = {key for key, value in ctx.params.to_external().items() if value != before[key]}
    expected = {step for key in changed for step in impacts.get(key, [])}
    actual = {key for key, value in ctx.states.items() if value["status"] == "stale"}
    assert actual == expected
    assert ctx.params.to_external()[tag] == changes[tag]
    assert ctx.params.to_external()[other] == changes[other]
    assert ctx.revision == 1 and notices and ctx.devices == {}
    with pytest.raises(ValueError):
        ctx.update_parameters({"not_a_parameter": 1})
    assert ctx.revision == 1 and ctx.devices == {}
    assert not list(tmp_path.rglob("*.npz"))


def test_fid_fit_small_numeric_sentinel():
    """400 点覆盖可辨识衰减与整周期；只检查数值，不重复渲染。"""
    import numpy as np
    from lab_workflows.experiment_modules.t2_calibration.analysis import fit_t2_diagnostics
    time = np.arange(400, dtype=float) / 400000
    waveform = .2 * np.exp(-time / .002) * np.cos(2 * np.pi * 90000 * time) + .01
    result = fit_t2_diagnostics(time, waveform)
    assert result.success and result.reason == "ok"
    assert result.t2_ms == pytest.approx(2.0, rel=1e-3)
    assert result.optimum.shape == (5,) and np.isfinite(result.optimum).all()
    assert result.optimum[2] == pytest.approx(90000, rel=1e-5)


@pytest.mark.parametrize("invalid", ["duplicate", "unknown_dependency", "forward_dependency",
    "analysis_role", "same_role", "dependent_finish"])
def test_flow_rejects_invalid_structure_without_guessing_roles(invalid):
    def stage(identifier, dependencies=()):
        return dict(id=identifier, title="不用于推断角色", group="测试", dependencies=dependencies,
            parameters=[], display_parameters=[], artifacts=[])
    value = dict(run_step_id="collect", finish_step_id="stop",
        acquisition=[stage("collect"), stage("stop")], analysis=[stage("reduce")],
        parameter_impacts=dict(acquisition={}, analysis={}), diagram_url="/diagram")
    if invalid == "duplicate":
        value["analysis"][0]["id"] = "collect"
    elif invalid == "unknown_dependency":
        value["analysis"][0]["dependencies"] = ["missing"]
    elif invalid == "forward_dependency":
        value["acquisition"][0]["dependencies"] = ["stop"]
    elif invalid == "analysis_role":
        value["run_step_id"] = "reduce"
    elif invalid == "same_role":
        value["run_step_id"] = "stop"
    else:
        value["acquisition"][1]["dependencies"] = ["collect"]
    with pytest.raises(ValidationError):
        XYFlow.model_validate(value)
