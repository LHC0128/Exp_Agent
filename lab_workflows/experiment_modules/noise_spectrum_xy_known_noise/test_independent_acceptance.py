"""独立验收：无效输入先拒绝，旧科学产物及通用入口边界受保护。"""
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock, patch

import numpy as np
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ...plotting import new_figure, save_figure
from .analysis import AnalysisWorkflow
from .stages import EXPERIMENT_ID
from .test_workflow import copy_known_config, rig, scan_params


def seeded_run(root):
    copy_known_config(root)
    run = root / "data/Noise_Spectrum_XY_Ctrl_Known_Noise/history"
    (run / "raw").mkdir(parents=True)
    (run / "results").mkdir()
    for state in ("on", "off"):
        np.save(run / f"raw/waveform_C0000_{state}.npy", np.sin(np.arange(256)))
    config = dict(experiment_id=EXPERIMENT_ID, schema_version=5, completion_status="completed",
        parameters={}, hf2_daq=dict(actual_rate_Sa_s=1000., DAQ_duration_s=1.),
        scan_params=dict(xy_envelope_voltage_V=np.linspace(0., 2., 21).tolist()),
        known_noise=dict(waveform_file="raw/known_noise_waveform.npy"),
        truth_chain=dict(K_Z_Hz_per_V=5373.638, z_calibration_run=""))
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    np.savez(run / "results/psd_matrices.npz", freq_axis=np.arange(4.),
             psd_on=np.ones((21, 4)), psd_off=np.full((21, 4), .5))
    (run / "results/analysis_summary.json").write_text(
        json.dumps(dict(status="completed", psd=dict(actual_rate_sa_s=1000.))), encoding="utf-8")
    fig, ax = new_figure()
    ax.plot([0., 1.], [.5, 1.])
    save_figure(fig, run / "results/noise_spectrum_2d.png")
    return run, config


@pytest.mark.parametrize("section,key,value", [
    ("hf2_daq", "actual_rate_Sa_s", 0.),
    ("hf2_daq", "actual_rate_Sa_s", float("nan")),
    ("hf2_daq", "DAQ_duration_s", 0.),
    ("hf2_daq", "DAQ_duration_s", float("nan")),
    ("scan_params", "xy_envelope_voltage_V", [0., 0., 1.]),
    ("scan_params", "xy_envelope_voltage_V", [0., float("nan"), 1.]),
    ("scan_params", "xy_envelope_voltage_V", 1.),
])
def test_invalid_config_rejected_before_old_science_is_archived(tmp_path, section, key, value):
    run, config = seeded_run(tmp_path)
    before = {path.name: path.read_bytes() for path in (run / "results").iterdir()}
    config[section][key] = value
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    ctx = AnalysisWorkflow(run, project_root=tmp_path)
    with pytest.raises((ValueError, TypeError)):
        ctx.execute(["B01", "B02"])
    assert ctx.states["B01"]["status"] == "quality_failed"
    assert ctx.states["B02"]["status"] != "completed"
    assert {path.name: path.read_bytes() for path in (run / "results").iterdir()} == before
    assert not list((run / "results").glob("before_*"))


@pytest.mark.parametrize("mode", ["zero", "nan", "pair_mismatch", "within_state_mismatch"])
def test_invalid_sidecar_rates_rejected_before_old_science_is_archived(tmp_path, mode):
    run, _ = seeded_run(tmp_path)
    if mode == "within_state_mismatch":
        np.save(run / "raw/waveform_C0001_on.npy", np.sin(np.arange(256)))
        name, rate = "waveform_C0001_on_acquisition.npz", 500.
    else:
        name = "waveform_C0000_on_acquisition.npz"
        rate = {"zero": 0., "nan": float("nan"), "pair_mismatch": 500.}[mode]
    np.savez(run / "raw" / name, actual_rate_Sa_s=rate)
    before = {path.name: path.read_bytes() for path in (run / "results").iterdir()}
    ctx = AnalysisWorkflow(run, project_root=tmp_path)
    with pytest.raises(ValueError, match="实际采样率"):
        ctx.execute(["B01", "B02"])
    assert ctx.states["B01"]["status"] == "quality_failed"
    assert {path.name: path.read_bytes() for path in (run / "results").iterdir()} == before


def test_equal_saved_sidecar_rates_may_override_run_fallback(tmp_path):
    from . import analysis_core
    from .analysis_steps import step_B01

    run, _ = seeded_run(tmp_path)
    for state in ("on", "off"):
        np.savez(run / f"raw/waveform_C0000_{state}_acquisition.npz", actual_rate_Sa_s=500.)
    with patch.object(analysis_core, "welch", side_effect=AssertionError("B01 不计算 PSD")) as welch:
        step_B01(AnalysisWorkflow(run, project_root=tmp_path))
    welch.assert_not_called()


def test_changed_welch_budget_rejected_before_b02_recompute_archives_old_science(tmp_path):
    from .test_analysis_acceptance import write_run

    run = write_run(tmp_path, "budget")
    with redirect_stdout(StringIO()):
        AnalysisWorkflow(run).execute(["B01", "B02"])
    before = {path.name: path.read_bytes() for path in (run / "results").iterdir() if path.is_file()}
    backups = set((run / "results").glob("before_*"))
    ctx = AnalysisWorkflow(run, {"ANALYSIS_BIN_WIDTH_HZ": .1})
    assert ctx.states["B01"]["status"] == "completed"
    assert ctx.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="Welch"), redirect_stdout(StringIO()):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert {path.name: path.read_bytes() for path in (run / "results").iterdir() if path.is_file()} == before
    assert set((run / "results").glob("before_*")) == backups


def test_editable_diagram_quality_text_and_metadata_match_real_stages():
    from .stages import ACQUISITION, ANALYSIS

    path = Path(__file__).parent / "XY 已知可控噪声谱.excalidraw"
    scene = json.loads(path.read_text(encoding="utf-8"))
    elements = {item["id"]: item for item in scene["elements"] if not item.get("isDeleted")}
    normalized = lambda text: "".join(text.split())
    for stage in (*ACQUISITION, *ANALYSIS):
        if not stage.quality_gates:
            continue
        note = elements["quality-" + stage.id]
        assert note["customData"]["workflow_stage"] == stage.id
        assert note["customData"]["quality_gates"] == list(stage.quality_gates)
        for gate in stage.quality_gates:
            assert normalized(gate) in normalized(note["text"])
            assert normalized(gate) in normalized(note["originalText"])


def test_missing_identity_rejected_by_both_analysis_entries_and_listing(tmp_path):
    from GUI.backend import main
    from GUI.backend.jobs import JobManager
    from GUI.backend.noise_spectrum_xy_known_noise import KnownNoiseSessionService
    from GUI.backend.workflow_sessions import create_router

    run, config = seeded_run(tmp_path)
    config.pop("experiment_id")
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    manager = JobManager()
    service = KnownNoiseSessionService(lambda: tmp_path, manager)
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as specific, TestClient(main.app) as legacy, \
            patch.object(main, "ROOT", tmp_path), patch.object(main, "manager", manager), \
            patch.object(main, "known_noise_sessions", service), \
            patch.object(manager, "create", return_value=Mock(public=lambda: {"id": "invalid"})) as create:
        base = f"/api/experiments/{EXPERIMENT_ID}"
        assert specific.get(base + "/runs/history/analysis").status_code == 422
        response = legacy.post("/api/runs/history/analyze", json={"experiment_id": EXPERIMENT_ID})
        assert response.status_code in (400, 422), response.text
        create.assert_not_called()
        listed = legacy.get("/api/runs", params={"experiment_id": EXPERIMENT_ID}).json()["runs"]
        assert not any(item["can_analyze"] for item in listed if item["id"] == "history")


@pytest.mark.parametrize("section,key,value", [
    ("hf2_daq", "actual_rate_Sa_s", 0.),
    ("hf2_daq", "actual_rate_Sa_s", True),
    ("hf2_daq", "DAQ_duration_s", float("nan")),
    ("scan_params", "xy_envelope_voltage_V", [0., 0.]),
])
def test_legacy_entry_and_listing_reject_invalid_config_before_scheduling(tmp_path, section, key, value):
    from GUI.backend import main
    from GUI.backend.jobs import JobManager
    from GUI.backend.noise_spectrum_xy_known_noise import KnownNoiseSessionService

    run, config = seeded_run(tmp_path)
    config[section][key] = value
    (run / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    manager = JobManager()
    service = KnownNoiseSessionService(lambda: tmp_path, manager)
    with TestClient(main.app) as client, patch.object(main, "ROOT", tmp_path), \
            patch.object(main, "manager", manager), patch.object(main, "known_noise_sessions", service), \
            patch.object(manager, "create") as create:
        assert client.post("/api/runs/history/analyze", json={"experiment_id": EXPERIMENT_ID}).status_code == 400
        create.assert_not_called()
        listing = client.get("/api/runs", params={"experiment_id": EXPERIMENT_ID}).json()["runs"]
        assert [item["can_analyze"] for item in listing if item["id"] == "history"] == [False]


@pytest.mark.parametrize("first", ["legacy", "stepwise"])
def test_legacy_and_stepwise_analysis_share_directory_lock(tmp_path, first):
    from GUI.backend import main
    from GUI.backend.jobs import JobManager
    from GUI.backend.noise_spectrum_xy_known_noise import KnownNoiseSessionService
    from GUI.backend.workflow_sessions import create_router
    from .definition import ADAPTER

    seeded_run(tmp_path)
    manager = JobManager()
    service = KnownNoiseSessionService(lambda: tmp_path, manager)
    app = FastAPI()
    app.include_router(create_router(service))
    entered, release = Event(), Event()

    def execute(*args, **kwargs):
        entered.set()
        assert release.wait(15), "分析屏障未释放"

    base = f"/api/experiments/{EXPERIMENT_ID}"
    with TestClient(app) as specific, TestClient(main.app) as legacy, \
            patch.object(main, "ROOT", tmp_path), patch.object(main, "manager", manager), \
            patch.object(main, "known_noise_sessions", service), patch.object(ADAPTER, "_execute", execute):
        start_old = lambda: legacy.post("/api/runs/history/analyze", json={"experiment_id": EXPERIMENT_ID})
        start_steps = lambda: specific.post(base + "/runs/history/analysis",
            json={"revision": 0, "step_ids": ["B01"]})
        start, other = (start_old, start_steps) if first == "legacy" else (start_steps, start_old)
        response = start()
        assert response.status_code == 200, response.text
        job = manager.get(response.json()["id"])
        try:
            assert entered.wait(10)
            assert other().status_code == 409
            assert len(manager.jobs) == 1
            assert not manager.hardware_lock.locked()
        finally:
            release.set()
            deadline = monotonic() + 10
            while job.status in ("queued", "running") and monotonic() < deadline:
                sleep(.01)
        assert job.status == "completed", job.error


@pytest.mark.parametrize("finish", [False, True])
def test_restoration_uses_executed_phase_parameters_after_draft_update(tmp_path, finish):
    from . import acquisition, stages, workflow
    from ..noise_spectrum_xy import acquisition as sine_acquisition

    copy_known_config(tmp_path)
    with rig(tmp_path):
        ctx = workflow.Workflow(scan_params(), project_root=tmp_path)
        ctx.execute([stage.id for stage in stages.ACQUISITION[:13]])
        original = ctx.phase_parameters
        config = (ctx.run_dir / "experiment_config.yaml").read_bytes()
        with patch.object(acquisition, "restore_demod", wraps=acquisition.restore_demod) as explicit, \
                patch.object(sine_acquisition, "restore_demod", wraps=sine_acquisition.restore_demod) as closing:
            ctx.update_parameters({"HF2_DEMOD_TC": original.hf2_demod_tc * 2})
            explicit.assert_not_called()
            closing.assert_not_called()
            if finish:
                ctx.execute(["A15"])
                closing.assert_called_once()
                assert closing.call_args.args[1] is original
            else:
                # 直接验收恢复处理器；选择层仍要求扫描前置有效。
                ctx._execute_step("A14")
                explicit.assert_called_once()
                assert explicit.call_args.args[1] is original
                ctx.finish()
        assert (ctx.run_dir / "experiment_config.yaml").read_bytes() == config
        assert ctx.closed and ctx.shutdown["completed"]
