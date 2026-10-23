"""以真实采集及写盘配假仪器验证续测；不访问实验硬件。"""
from dataclasses import replace
import hashlib
import numpy as np
import pytest
import yaml
from .test_workflow import scale_rig, rig, control_source, prepared
from . import workflow, stages
from .analysis_steps import load_inputs
from ..rf_sensitivity_direct_aw import acquisition as direct, phase, preparation as shared
from ...common import WorkflowCancelled
from ...steps.temperature import configure_temperature_control


def partial_run(scale_rig, monkeypatch):
    scale_rig.params = replace(scale_rig.params, control_waveform_files=scale_rig.params.control_waveform_files[:1])
    ctx = prepared(scale_rig)
    real_noise = direct.acquire_noise
    with monkeypatch.context() as patch:
        def noise(point):
            if point.state["control_scale"] == 1.0:
                raise WorkflowCancelled("模拟响应后噪声中断")
            return real_noise(point)
        patch.setattr(direct, "acquire_noise", noise)
        with pytest.raises(WorkflowCancelled) as error:
            ctx.execute(["A13"])
        ctx.finish(error.value)
    return ctx.directory.root


def test_manual_phase_resume_reuses_only_same_mode_and_phase(scale_rig, monkeypatch):
    from .resume import resume_plan
    scale_rig.params = replace(scale_rig.params, scan_rf_phase=False, z_burst_phase_init=181.5)
    source = partial_run(scale_rig, monkeypatch)
    params = replace(scale_rig.params, resume_run_id=source.name,
        control_scale_start=.9, control_scale_stop=1.1, control_scale_points=3,
        control_scale_factors={"ControlA": 1.})
    _, _, points = resume_plan(params, scale_rig.tmp_path)
    assert len(points) == 1
    assert points[0][2]["rf_phase_calibration"]["mode"] == "manual"
    for changed in (replace(params, scan_rf_phase=True), replace(params, z_burst_phase_init=182.5)):
        with pytest.raises(ValueError, match="不一致"):
            resume_plan(changed, scale_rig.tmp_path)
    resumed = workflow.Workflow(params, project_root=scale_rig.tmp_path)
    try:
        resumed.execute([step.id for step in stages.ACQUISITION if step.id != "A16"])
        assert resumed.state["reused_points"] == 1
        config = yaml.safe_load(resumed.directory.config_path.read_text(encoding="utf-8"))
        completed = load_inputs(resumed.directory.root, config)
        assert len(completed) == 3
        assert all(child["rf_phase_calibration"]["selected_phase_deg"] == 181.5 for _, _, child in completed)
        assert not list(resumed.directory.root.glob("controls/*/scales/*/raw/z_phase_scan.npz"))
    finally:
        resumed.finish()


def test_resume_reuses_only_complete_point_and_never_connects_external_tec(scale_rig, monkeypatch):
    source = partial_run(scale_rig, monkeypatch)
    failed = source / "controls/ControlA/scales/scale_1.000000000/raw/partial_only.txt"
    failed.write_text("失败点原始诊断必须留在旧目录", encoding="utf-8")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob("*") if p.is_file()}
    params = replace(scale_rig.params, resume_run_id=source.name, tec_external_control=True, run_tag="resumed")
    calls = []
    for name in ("calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3"):
        real = getattr(phase, name)
        def calibrate(point, _name=name, _real=real):
            calls.append((point.state["control_scale"], _name))
            return _real(point)
        monkeypatch.setattr(phase, name, calibrate)
    def no_tec(*args, **kwargs):
        pytest.fail("外部温控模式绝不打开 TEC 串口")
    monkeypatch.setattr(shared, "TECInstrument", no_tec)
    monkeypatch.setattr(shared, "configure_temperature_control", configure_temperature_control)
    ctx = workflow.Workflow(params, project_root=scale_rig.tmp_path)
    ctx.execute([step.id for step in stages.ACQUISITION if step.id != "A16"])
    assert ctx.directory.root != source
    assert ctx.state["reused_points"] == 1
    assert ctx.state["calibration_points_completed"] == 3
    assert ctx.state["response_points_completed"] == 3 * params.rf_amp_points
    assert ctx.state["noise_records_completed"] == 3 * params.noise_n_avg
    assert ctx.state["bandwidth_points_completed"] == 3 * params.bw_freq_points
    assert calls == [(scale, name) for scale in (1.0, 1.1) for name in (
        "calibrate_demod0", "calibrate_xy", "calibrate_rf", "calibrate_demod3")]
    assert ctx.state["temperature_control"]["control_source"] == "external_software"
    assert ctx.state["temperature_control"]["actual_temperature_c"] is None
    with pytest.raises(ValueError, match="新建会话"):
        ctx.update_parameters({"TEC_EXTERNAL_CONTROL": False})
    config = yaml.safe_load((ctx.directory.root / "experiment_config.yaml").read_text(encoding="utf-8"))
    points = load_inputs(ctx.directory.root, config)
    assert len(points) == 3
    assert points[0][2]["resumed_from"]["run_id"] == source.name
    assert points[1][2]["temperature_control"]["control_source"] == "external_software"
    assert not (ctx.directory.root / failed.relative_to(source)).exists()
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in before.items())
    ctx.finish()


@pytest.mark.parametrize("fault", ["parameters", "compensation", "raw", "noise", "phase", "path", "active"])
def test_resume_rejects_changed_or_incomplete_source_before_connecting(scale_rig, monkeypatch, fault):
    source = partial_run(scale_rig, monkeypatch)
    params = replace(scale_rig.params, resume_run_id=source.name)
    point = source / "controls/ControlA/scales/scale_0.900000000"
    if fault == "parameters":
        params = replace(params, z_v_to_nt=params.z_v_to_nt + 1)
    elif fault == "compensation":
        params = replace(params, control_scale_factors={"ControlA": 1.01})
    elif fault == "raw":
        file = point / "raw/response_data.npz"
        with np.load(file, allow_pickle=False) as data:
            arrays = {key: data[key] for key in data.files}
        arrays["demod3_x_V"] = arrays["demod3_x_V"][:-1]
        np.savez(file, **arrays)
    elif fault == "noise":
        next((point / "raw").glob("noise_D3_*.npz")).unlink()
    elif fault == "phase":
        file = point / "experiment_config.yaml"
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        config["phase_calibration_steps"]["A12"]["status"] = "failed"
        file.write_text(yaml.safe_dump(config), encoding="utf-8")
    elif fault == "path":
        params = replace(params, resume_run_id="../" + source.name)
    elif fault == "active":
        file = source / "experiment_config.yaml"
        config = yaml.safe_load(file.read_text(encoding="utf-8"))
        config["completion_status"] = "running"
        file.write_text(yaml.safe_dump(config), encoding="utf-8")
    scale_rig.events.clear()
    with pytest.raises(ValueError):
        workflow.Workflow(params, project_root=scale_rig.tmp_path)
    assert not scale_rig.events
