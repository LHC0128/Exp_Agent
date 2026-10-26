"""分析数值、历史覆盖隔离、产物指纹与分支归档回归。"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps
from .test_dc_scan import make_run
from .stages import ANALYSIS, definition


REGRESSION_CASES = {
    "static-sensitivity": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/static_sensitivity/analysis.py",
                "lab_workflows/experiment_modules/static_sensitivity/definition.py",
                "lab_workflows/experiment_modules/static_sensitivity/__init__.py"
            ],
            "tests": [
                "test_complete_and_staged_are_numerically_identical_and_get_is_read_only",
                "test_override_only_invalidates_fit_and_sensitivity_preserving_psd",
                "test_unsupported_overrides_do_not_write",
                "test_missing_waveforms_quality_failures_and_diagnostics",
                "test_missing_saved_waveform_is_rejected_even_when_another_is_present",
                "test_invalid_quality_keeps_diagnostic_fit_and_refuses_sensitivity",
                "test_nested_raw_config_and_product_fingerprints_reject_stale_reuse",
                "test_original_v3_starts_from_b01_and_running_or_old_runs_are_rejected",
                "test_noise_rate_sidecar_changes_invalidate_and_rebuild_real_psd",
                "test_raw_format_is_independent_of_parameter_schema_and_keeps_verified_history",
                "test_real_module_requires_explicit_run_outside_repository",
                "test_package_public_exports_remain_compatible",
                "test_registered_adapter_uses_explicit_directory_and_clears_inherited_context"
            ]
        }
    }
}


@pytest.fixture
def run_dir(tmp_path):
    make_run(tmp_path)
    return tmp_path


def test_complete_and_staged_are_numerically_identical_and_get_is_read_only(run_dir):
    config_path=run_dir / "experiment_config.yaml"
    original=config_path.read_bytes()
    full=analysis.analyze(run_dir)
    with np.load(run_dir / "results/gs200_current_range_sensitivity.npz") as data:
        expected={key:data[key] for key in data.files}
    ctx=analysis.AnalysisWorkflow(run_dir)
    assert all(state["status"]=="completed" for state in ctx.states.values())
    for stage in ANALYSIS:
        ctx=analysis.AnalysisWorkflow(run_dir)
        ctx.execute([stage.id],ctx.revision)
    for key in ("fit_center_v","fit_slope_V_per_fT","fit_gamma_fT","sensitivity_flat_fT_per_sqrt_Hz"):
        assert ctx.summary[key]==full[key]
    with np.load(run_dir / "results/gs200_current_range_sensitivity.npz") as data:
        for key,values in expected.items():
            np.testing.assert_allclose(data[key],values)
    assert config_path.read_bytes()==original
    before=(run_dir / "analysis_state.json").read_bytes()
    analysis.AnalysisWorkflow(run_dir).snapshot()
    assert before==(run_dir / "analysis_state.json").read_bytes()
    assert config_path.read_bytes()==original


def test_override_only_invalidates_fit_and_sensitivity_preserving_psd(run_dir):
    full=analysis.analyze(run_dir)
    snapshot=(run_dir / "experiment_config.yaml").read_bytes()
    psd=(run_dir / "results/noise_psd.npz").read_bytes()
    before=analysis.AnalysisWorkflow(run_dir)
    ctx=analysis.AnalysisWorkflow(run_dir,{"z_v_to_nt":3900.})
    assert ctx.states["B01"]["status"]==ctx.states["B03"]["status"]=="completed"
    assert {key for key,state in ctx.states.items() if state["status"]=="stale"}==set(definition()["parameter_impacts"]["analysis"]["z_v_to_nt"])
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B04"])
    result=ctx.execute(["B02","B04"],before.revision)
    assert result["fit_slope_V_per_fT"]==pytest.approx(full["fit_slope_V_per_fT"]/2)
    assert (run_dir / "results/noise_psd.npz").read_bytes()==psd
    assert (run_dir / "experiment_config.yaml").read_bytes()==snapshot
    backups=list((run_dir / "results").glob("before_*"))
    assert any((path / "dispersion_fit.npz").exists() for path in backups)
    assert not any((path / "noise_psd.npz").exists() for path in backups)
    assert analysis.AnalysisWorkflow(run_dir).options["z_v_to_nt"]==3900.
    assert yaml.safe_load(snapshot)["parameters"]["z_v_to_nt"]==1950


@pytest.mark.parametrize("overrides",[{"z_v_to_nt":0},{"z_v_to_nt":-1},{"z_v_to_nt":True},{"z_v_to_nt":float('nan')},{"z_v_to_nt":float('inf')},{"z_v_to_nt":"1950"},{"noise_duration":1}])
def test_unsupported_overrides_do_not_write(run_dir,overrides):
    with pytest.raises(ValueError):
        analysis.AnalysisWorkflow(run_dir,overrides)
    assert not (run_dir / "analysis_state.json").exists()
    assert not (run_dir / "results").exists()


def test_missing_waveforms_quality_failures_and_diagnostics(run_dir):
    noise=next((run_dir / "raw").rglob("*.npy"))
    noise.unlink()
    ctx=analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(FileNotFoundError,match="噪声"):
        ctx.execute(["B01"])
    assert ctx.states["B01"]["status"]=="failed"
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B03"])
    assert json.loads((run_dir / "results/analysis.json").read_text(encoding="utf-8"))["success"] is False


def test_missing_saved_waveform_is_rejected_even_when_another_is_present(run_dir):
    config_path=run_dir / "experiment_config.yaml"
    config=yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gs200_range_measurements"][0]["samples_saved"]=2
    config_path.write_text(yaml.safe_dump(config),encoding="utf-8")
    ctx=analysis.AnalysisWorkflow(run_dir)
    with pytest.raises(ValueError,match="波形数与采集完成记录不一致"):
        ctx.execute(["B01"])
    assert ctx.states["B01"]["status"]=="quality_failed"
    assert ctx.summary["success"] is False


def test_invalid_quality_keeps_diagnostic_fit_and_refuses_sensitivity(run_dir):
    ctx=analysis.AnalysisWorkflow(run_dir)
    ctx.execute(["B01","B03"])
    fit=analysis_steps.fit_dispersive(np.linspace(-.2,.2,81),analysis_steps._dispersive(np.linspace(-.2,.2,81),.0003,.025,.007,.001),1950,1950e6)
    fit.is_valid=False
    fit.rejection_reasons=["质量门槛失败"]
    with patch.object(analysis_steps,"fit_dispersive",return_value=fit),pytest.raises(ValueError,match="质量不通过"):
        ctx.execute(["B02"])
    assert ctx.states["B02"]["status"]=="quality_failed"
    with np.load(run_dir / "results/dispersion_fit.npz") as data:
        assert not bool(data["fit_valid"])
        assert data["rejection_reasons"].tolist()==["质量门槛失败"]
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B04"])
    assert not (run_dir / "results/gs200_current_range_sensitivity.npz").exists()


def test_nested_raw_config_and_product_fingerprints_reject_stale_reuse(run_dir):
    analysis.analyze(run_dir)
    psd=run_dir / "results/noise_psd.npz"
    psd.write_bytes(b"corrupt")
    ctx=analysis.AnalysisWorkflow(run_dir)
    assert ctx.states["B02"]["status"]=="completed"
    assert ctx.states["B03"]["status"]==ctx.states["B04"]["status"]=="stale"
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B04"])
    ctx.execute(["B03","B04"])
    noise=next((run_dir / "raw").rglob("*.npy"))
    np.save(noise,np.zeros(1000))
    assert all(state["status"]=="stale" for state in analysis.AnalysisWorkflow(run_dir).states.values())
    with pytest.raises(ValueError,match="已改变"):
        analysis.AnalysisWorkflow(run_dir).execute(["B01"],ctx.revision)


def test_original_v3_starts_from_b01_and_running_or_old_runs_are_rejected(run_dir):
    ctx=analysis.AnalysisWorkflow(run_dir)
    assert all(state["status"]=="pending" for state in ctx.states.values())
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B02"])
    path=run_dir / "experiment_config.yaml"
    config=yaml.safe_load(path.read_text(encoding="utf-8"))
    config["completion_status"]="running"
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises(ValueError,match="写入"):
        analysis.AnalysisWorkflow(run_dir)
    config["schema_version"]=2
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises(ValueError,match="旧格式"):
        analysis.AnalysisWorkflow(run_dir)


def test_noise_rate_sidecar_changes_invalidate_and_rebuild_real_psd(run_dir):
    analysis.analyze(run_dir)
    waveform=next((run_dir / "raw").rglob("noise_*.npy"))
    sidecar=waveform.with_name(waveform.stem+"_acquisition.npz")
    np.savez(sidecar,actual_rate_Sa_s=2000.)
    ctx=analysis.AnalysisWorkflow(run_dir)
    assert all(state["status"]=="stale" for state in ctx.states.values())
    assert "noise_raw/gs200_range_10mA/noise_0000_acquisition.npz" in ctx.fingerprint["raw_files"]
    with pytest.raises(ValueError,match="缺少前置"):
        ctx.execute(["B04"])
    ctx.execute([stage.id for stage in ANALYSIS])
    with np.load(run_dir / "results/noise_psd.npz") as psd:
        assert psd["freq"][-1]==1000.
    sidecar.unlink()
    assert all(state["status"]=="stale" for state in analysis.AnalysisWorkflow(run_dir).states.values())


@pytest.mark.parametrize("schema,raw_version,allowed",[(3,None,True),(4,None,True),(99,3,True),(99,None,False),(4,99,False),(4,True,False)])
def test_raw_format_is_independent_of_parameter_schema_and_keeps_verified_history(run_dir,schema,raw_version,allowed):
    path=run_dir / "experiment_config.yaml"
    config=yaml.safe_load(path.read_text(encoding="utf-8"))
    config["schema_version"]=schema
    if raw_version is not None:
        config["raw_format_version"]=raw_version
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    original=path.read_bytes()
    if allowed:
        ctx=analysis.AnalysisWorkflow(run_dir)
        assert all(state["status"]=="pending" for state in ctx.states.values())
    else:
        with pytest.raises(ValueError,match="格式"):
            analysis.AnalysisWorkflow(run_dir)
    assert path.read_bytes()==original
    assert not (run_dir / "analysis_state.json").exists()

@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_module_requires_explicit_run_outside_repository(tmp_path, mode):
    run = tmp_path / "explicit"
    run.mkdir()
    make_run(run)
    before = {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="p1-2-remaining-cwd-") as temporary:
        cwd = Path(temporary).resolve()
        repository = Path(__file__).resolve().parents[3]
        assert not cwd.is_relative_to(repository)
        decoy = cwd / "data" / "Static_Magnetic_Field_Sensitivity" / "9999_latest"
        shutil.copytree(run, decoy)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_") and k != "PYTHONPATH"}
        env.update(MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        missing = cwd / "not-a-run"
        if mode != "missing":
            env["LAB_RUN_DIR"] = str(run if mode == "explicit" else missing)
        command = [sys.executable, "-X", "utf8", "-m",
            "lab_workflows.experiment_modules.static_sensitivity.analysis"]
        started = time.monotonic()
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        print(json.dumps(dict(command=command, cwd=str(cwd), mode=mode,
            lab_environment={k: v for k, v in env.items() if k.startswith("LAB_")},
            returncode=result.returncode, elapsed_seconds=time.monotonic() - started), ensure_ascii=False))
        print(result.stdout, result.stderr)
        assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
        assert not missing.exists()
        assert {p: (run / p).read_bytes() for p in before if p.parts[0] == "raw"} == {
            p: value for p, value in before.items() if p.parts[0] == "raw"}
        if mode == "explicit":
            assert result.returncode == 0, result.stdout + result.stderr
            assert (run / "results/gs200_current_range_sensitivity.npz").is_file()
            assert json.loads((run / "analysis_state.json").read_text(encoding="utf-8"))["steps"]["B04"]["status"] == "completed"
            assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == yaml.safe_load(before[Path("experiment_config.yaml")])["parameters"]
        else:
            assert {p.relative_to(run): p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
            assert result.returncode != 0
            assert ("缺少 LAB_RUN_DIR" if mode == "missing" else "运行目录不存在") in result.stderr


def test_package_public_exports_remain_compatible():
    from . import definition
    from .models import StaticSensitivityParams

    package = sys.modules[__package__]
    assert package.__all__ == ["ADAPTER","DEFINITION","StaticSensitivityParams"]
    assert package.DEFINITION is definition.DEFINITION
    assert package.ADAPTER is definition.ADAPTER
    assert package.StaticSensitivityParams is StaticSensitivityParams
    with pytest.raises(AttributeError, match="unknown_public_symbol"):
        getattr(package, "unknown_public_symbol")


def test_registered_adapter_uses_explicit_directory_and_clears_inherited_context(tmp_path, monkeypatch):
    from lab_workflows.experiments import get_experiment, typed

    root = tmp_path / "project"
    root.mkdir()
    (root / "params").mkdir()
    repository = Path(__file__).resolve().parents[3]
    for name in ("devices.yaml", "mapping.yaml", "safety_limits.yaml"):
        shutil.copy2(repository / "params" / name, root / "params" / name)
    run = root / "run"
    run.mkdir()
    make_run(run)
    before_parameters = yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    decoy = run.with_name("9999_latest")
    shutil.copytree(run, decoy)
    before = {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
    adapter = get_experiment("static-sensitivity").analysis_runner.__self__
    monkeypatch.setattr(adapter, "root", root)
    popen, launches = typed.subprocess.Popen, []

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        return popen([sys.executable, *command[1:]], **kwargs)

    monkeypatch.setattr(typed.subprocess, "Popen", launch)
    monkeypatch.setenv("LAB_RUN_DIR", str(decoy))
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", "invalid old context")
    result = adapter.analyze(run, None)
    assert len(launches) == 1
    command, kwargs = launches[0]
    assert command[-2:] == ["-m", "lab_workflows.experiment_modules.static_sensitivity.analysis"]
    assert kwargs["cwd"] == root and not root.is_relative_to(repository)
    assert kwargs["env"]["LAB_RUN_DIR"] == str(run.resolve())
    assert "LAB_TYPED_PARAMETERS" not in kwargs["env"]
    assert result["run_dir"] == str(run.resolve()) and result["artifacts"]
    assert yaml.safe_load((run / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == before_parameters
    assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
    assert not list((root / "data/.gui_jobs").glob("*.cancel"))
