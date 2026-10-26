"""多控制的真实假仪器采集、故障抢救、独立 PSD 筛选和缓存回归。"""
from dataclasses import replace
from pathlib import Path
import json
import time
import numpy as np
import pytest
import yaml
from lockin_amplifier import DAQResult
from ...common import WorkflowCancelled
from ...control_sources import resolve_control_results_root
from ...steps import RunDirectory
from .test_workflow import rig, control_source, assert_shutdown
from ..rf_sensitivity_aw_known_noise.test_workflow import known_rig
from . import workflow, acquisition, control_runs, _helpers, analysis, analysis_steps
from ..rf_sensitivity_aw_known_noise import workflow as known_workflow, analysis as known_analysis

ACQUISITION_CASES = {
    "rf-sensitivity-aw": {
        "mode": "grouped",
        "sources": ["lab_workflows/experiment_modules/rf_sensitivity_direct_aw/workflow.py",
                    "lab_workflows/experiment_modules/rf_sensitivity_direct_aw/control_runs.py"],
        "cases": {
            "entrypoints": ["test_multi_order_slow_disk_compensation_and_last_group"],
            "order": ["test_multi_order_slow_disk_compensation_and_last_group"],
            "slow_disk": ["test_multi_order_slow_disk_compensation_and_last_group"],
            "group_change": ["test_multi_order_slow_disk_compensation_and_last_group"],
            "last_group": ["test_multi_order_slow_disk_compensation_and_last_group"],
            "cancel": ["test_second_control_failure_preserves_both_partial_runs"],
            "acquire_failure": ["test_second_control_failure_preserves_both_partial_runs"],
            "partial_save": ["test_restore_or_save_failure_preserves_read_data"],
            "restore_failure": ["test_restore_or_save_failure_preserves_read_data"],
            "save_failure": ["test_restore_or_save_failure_preserves_read_data"],
        },
    },
}


@pytest.fixture
def multi_rig(rig, monkeypatch):
    source = resolve_control_results_root("oc_sens")
    second = source / "v2/waveforms/optimal_control_waveform.csv"
    second.parent.mkdir(parents=True)
    second.write_bytes((source / rig.params.arb_waveform_file).read_bytes())
    rig.params = replace(rig.params, control_waveform_files=[rig.params.arb_waveform_file,
        "v2/waveforms/optimal_control_waveform.csv"], control_scale_factors={"v1": 1.0, "v2": 0.75},
        scan_rf_phase=False, z_burst_phase_init=37, temp_group_size=2, noise_n_avg=2)
    monkeypatch.setattr(control_runs, "wait", lambda *_: _helpers.check_cancelled())
    monkeypatch.setattr(control_runs, "check_cancelled", _helpers.check_cancelled)
    return rig


def test_multi_order_slow_disk_compensation_and_last_group(multi_rig, monkeypatch):
    r = multi_rig
    temp = r.mapping["Temp_Switch"]
    writes, capture, save, update = [], acquisition.daq.acquire_data, np.savez, RunDirectory.update_config

    def level():
        return next((event[3] for event in reversed(r.events)
            if event[:3] == (temp["resource"], "setup_dc", temp["channel"])), None)

    def read(*args, **kwargs):
        assert level() == 0
        return capture(*args, **kwargs)

    def disk(path, *args, **kwargs):
        assert level() in (None, 5)
        writes.append(str(path))
        time.sleep(0.001)
        return save(path, *args, **kwargs)

    def metadata(directory, **kwargs):
        assert level() in (None, 5)
        return update(directory, **kwargs)

    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    monkeypatch.setattr(np, "savez", disk)
    monkeypatch.setattr(RunDirectory, "update_config", metadata)
    directory = workflow.run(r.params)
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["raw_format_version"] == 2 and config["control_layout"] == "controls"
    assert config["response_points_completed"] == 6 and config["noise_records_completed"] == 4
    assert not (directory / "raw/noise_waveform.npz").exists()
    for version, factor in (("v1", 1.0), ("v2", 0.75)):
        child = directory / "controls" / version
        child_config = yaml.safe_load((child / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert child_config["completion_status"] == child_config["phase_calibration_status"] == "completed"
        assert child_config["raw_format_version"] == 1
        assert len(list((child / "raw").glob("noise_D3_*.npz"))) == 2
        with np.load(child / "raw/xy_aw_waveforms.npz") as data:
            assert float(data["control_scale_factor"]) == factor
        with np.load(child / "raw/response_data.npz") as data:
            assert len(data["amplitudes_V"]) == 3
            assert data["z_burst_phase_deg"].tolist() == [217, 37, 37]
    z = r.mapping["Z_magnetic_field"]
    assert not any(event[:3] == (z["resource"], "setup_arbitrary", z["channel"]) for event in r.events)
    assert any("v1" in path for path in writes) and any("v2" in path for path in writes)
    assert_shutdown(r)


def test_all_selected_controls_are_checked_before_connect(multi_rig):
    with pytest.raises(ValueError):
        workflow.Workflow(replace(multi_rig.params, control_scale_factors={"v2": 1e8}))
    assert not any(event[1] == "connect" for event in multi_rig.events)
    for files in ([], [multi_rig.params.control_waveform_files[0]] * 2):
        with pytest.raises(ValueError):
            workflow.Workflow(replace(multi_rig.params, control_waveform_files=files))


@pytest.mark.parametrize("cancel", [False, True])
def test_second_control_failure_preserves_both_partial_runs(multi_rig, monkeypatch, cancel):
    r = multi_rig
    r.params = replace(r.params, rf_amp_points=2, noise_rate=1000,
                       noise_duration=0.01, scan_point_duration_s=0.001)
    # 子运行契约不验证 GUI 通知；配置、原始数据与安全恢复仍走真实实现。
    monkeypatch.setattr(workflow.SingleControlWorkflow, "publish", lambda *_: None)
    capture, count = acquisition.daq.acquire_data, 0
    error = WorkflowCancelled("第二控制取消") if cancel else RuntimeError("第二控制采集故障")

    def read(hf2, config, **kwargs):
        nonlocal count
        if config.signal_paths == ["sample.x"]:
            count += 1
            if count == 4:
                raise error
        return capture(hf2, config, **kwargs)

    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    with pytest.raises(type(error), match=str(error)) as caught:
        workflow.run(r.params)
    assert caught.value is error
    directory = next((r.tmp_path / "data/RF_Field_Sensitivity_AW").iterdir())
    assert len(list((directory / "controls/v1/raw").glob("noise_D3_*.npz"))) == 2
    assert len(list((directory / "controls/v2/raw").glob("noise_D3_*.npz"))) == 1
    config = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    assert config["completion_status"] == ("cancelled" if cancel else "failed")
    assert config["failure_reason"] == str(error)
    for version, records in (("v1", 2), ("v2", 1)):
        child = yaml.safe_load((directory / "controls" / version / "experiment_config.yaml").read_text(encoding="utf-8"))
        assert child["completion_status"] == config["completion_status"]
        assert child["safety_shutdown"] == config["safety_shutdown"]
        assert child["noise_records_completed"] == records
        assert child["raw_format_version"] == 1 and child["control_layout"] == "single"
    assert_shutdown(r)


@pytest.mark.parametrize("fault", ["restore", "save"])
def test_restore_or_save_failure_preserves_read_data(multi_rig, monkeypatch, fault):
    r = multi_rig
    ctx = workflow.Workflow(r.params)
    ctx.execute([stage.id for stage in ctx.stages if stage.id not in ("A14", "A13B", "A15", "A16")])
    original = np.savez
    if fault == "restore":
        capture = acquisition.daq.acquire_data
        def read(*args, **kwargs):
            result = capture(*args, **kwargs)
            r.restore_error = True
            return result
        monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    else:
        def save(path, *args, **kwargs):
            if Path(path).name == "noise_D3_0001.npz":
                raise RuntimeError("多控制保存故障")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(np, "savez", save)
    failure = None
    try:
        with pytest.raises(RuntimeError) as caught:
            ctx.execute(["A14"])
        failure = caught.value
        assert (ctx.directory.root / "controls/v1/raw/noise_D3_0000.npz").is_file()
    finally:
        ctx.finish(failure)
    assert ctx.state["completion_status"] == "failed"
    assert ctx.state["failure_reason"] == str(failure)


def test_noise_rerun_copies_response_but_not_old_noise(multi_rig, monkeypatch):
    params = replace(multi_rig.params, rf_amp_points=2, noise_rate=1000,
                     noise_duration=0.01, scan_point_duration_s=0.001)
    monkeypatch.setattr(workflow.SingleControlWorkflow, "publish", lambda *_: None)
    factory = workflow.SingleControlWorkflow.create_control_run.__func__
    created = []

    def create(cls, params, **kwargs):
        before = len(multi_rig.events)
        child = factory(cls, params, **kwargs)
        assert len(multi_rig.events) == before and child.devices is kwargs["devices"]
        assert child.states == kwargs["stage_states"] and child.states is not kwargs["stage_states"]
        assert child.states["A01"] is not kwargs["stage_states"]["A01"]
        previous = kwargs["previous"]
        calibration = previous.state["calibration"] if previous else kwargs["shared_calibration"]
        assert child.state["calibration"] == calibration and child.state["calibration"] is not calibration
        assert child.state["raw_format_version"] == 1 and child.state["control_layout"] == "single"
        assert child.state["control_metrics"]["control_scale_factor"] == params.control_scale_factor(params.control_waveform_files[0])
        if previous:
            assert "A14" in kwargs["affected_stages"] and "A13" not in kwargs["affected_stages"]
            assert previous.states["A10"]["status"] == "pending"
            assert child.response_config is previous.response_config
            assert child.xy_phase is previous.xy_phase
            assert child.state["response_points_completed"] == params.rf_amp_points
            assert child.state["noise_records_completed"] == 0
        created.append(child)
        return child

    monkeypatch.setattr(workflow.SingleControlWorkflow, "create_control_run", classmethod(create))
    ctx = workflow.Workflow(params)
    try:
        ctx.execute([stage.id for stage in ctx.stages if stage.id not in ("A13B", "A15", "A16")])
        first = ctx.directory.root
        stale = []
        for child in ctx.controls.values():
            # 内存中的响应配置只看 affected；不为它新增 completed 门槛。
            child.states["A10"]["status"] = "pending"
            path = child.directory.raw / "noise_D3_obsolete.npz"
            np.savez(path, sentinel=np.array([123.]))
            stale.append((path, path.read_bytes()))
        ctx.execute(["A14"])
        assert first != ctx.directory.root
        for version in ("v1", "v2"):
            a, b = (root / "controls" / version / "raw" for root in (first, ctx.directory.root))
            assert (a / "response_data.npz").read_bytes() == (b / "response_data.npz").read_bytes()
            assert len(list(a.glob("noise_D3_*.npz"))) == 3  # 两条旧记录及额外哨兵。
            assert len(list(b.glob("noise_D3_*.npz"))) == 2
            assert not (b / "noise_D3_obsolete.npz").exists()
        assert len(created) == 4
        assert all(path.read_bytes() == before for path, before in stale)
    finally:
        ctx.finish()
    assert_shutdown(multi_rig)


@pytest.mark.parametrize("kind", ["direct", "known"])
def test_each_control_filters_independently_and_can_disable(request, monkeypatch, kind):
    r = request.getfixturevalue("multi_rig" if kind == "direct" else "known_rig")
    r.params = replace(r.params, noise_n_avg=5, noise_duration=0.1, noise_nperseg=8192,
        noise_outlier_policy="mad_high", scan_rf_phase=False, z_burst_phase_init=37)
    capture, count = acquisition.daq.acquire_data, 0

    def read(hf2, config, **kwargs):
        nonlocal count
        result = capture(hf2, config, **kwargs)
        if config.signal_paths == ["sample.x"]:
            gain = 20 if count == 4 else 100 if count >= 5 else 1
            count += 1
            n, rate = config.grid_cols, kwargs["actual_rate"]
            result = [DAQResult(signal_name="sample.x", demod_index=kwargs["demod_idx"],
                values=gain * np.random.default_rng(3).normal(0, 1e-4, n), time=np.arange(n) / rate)]
        return result

    monkeypatch.setattr(acquisition.daq, "acquire_data", read)
    module, analyzer = (workflow, analysis) if kind == "direct" else (known_workflow, known_analysis)
    directory = module.run(r.params)
    originals = {path: path.read_bytes() for path in directory.glob("controls/*/raw/noise_D3_*.npz")}
    result = analyzer.analyze(directory)
    assert result["noise_rejection"]["n_rejected"] == 1
    assert result["controls"]["v1"]["noise_rejection"]["n_accepted"] == 4
    assert result["controls"]["v2"]["noise_rejection"]["n_accepted"] == 5
    assert result["noise_state"] == ("OFF" if kind == "direct" else "ON")
    with np.load(directory / "controls/v1/results/psd_avg.npz") as data:
        np.testing.assert_allclose(data["psd_avg_V2_per_Hz"], data["psd_matrix_V2_per_Hz"][:4].mean(axis=0))
        assert data["included_record_mask"].tolist() == [True, True, True, True, False]
    ctx = analyzer.AnalysisWorkflow(directory, {"NOISE_OUTLIER_POLICY": "none"})
    assert ctx.states["B02"]["status"] == "completed" and ctx.states["B03"]["status"] == "stale"
    result = ctx.execute(["B03", "B04"])
    assert result["noise_rejection"]["n_rejected"] == 0
    with np.load(directory / "controls/v1/results/psd_avg.npz") as data:
        np.testing.assert_allclose(data["psd_avg_V2_per_Hz"], data["psd_matrix_V2_per_Hz"].mean(axis=0))
    assert all(path.read_bytes() == before for path, before in originals.items())
    assert (directory / "results/noise_record_quality.csv").is_file()
    assert list((directory / "controls/v1/results").glob("before_*"))
