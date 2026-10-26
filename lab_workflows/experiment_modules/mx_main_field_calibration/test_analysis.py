"""真实历史原始文件上的整次、分阶段分析与归档回归。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import numpy as np
import pytest
import yaml
from . import analysis, workflow, run_format
from .models import MxMainFieldCalibrationParams
from .test_workflow import rig


REGRESSION_CASES = {
    "mx-main-field-calibration": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_main_field_calibration/analysis.py",
                "lab_workflows/experiment_modules/mx_main_field_calibration/definition.py",
                "lab_workflows/experiment_modules/mx_main_field_calibration/__init__.py",
            ],
            "tests": [
                "test_complete_and_staged_analysis_share_calibration_and_archive_only_dependents",
                "test_history_without_index_and_raw_version_is_supported_but_not_unknown_identity",
                "test_missing_raw_and_modified_stage_artifacts_cannot_be_reused",
                "test_quality_failure_still_writes_diagnostics_and_original_nan_arrays",
                "test_raw_and_config_fingerprints_force_revalidation_and_keep_old_exports",
                "test_invalid_analysis_overrides_do_not_rewrite_history",
                "test_state_save_failure_keeps_original_config_bytes",
                "test_analysis_error_survives_secondary_state_save_and_cleanup_errors",
                "test_real_analysis_module_outside_repository_requires_explicit_directory",
                "test_real_registered_adapter_analysis_discards_old_run_context",
                "test_analysis_overrides_keep_model_bounds_without_hardware_revalidation",
            ],
        },
    },
}


def test_complete_and_staged_analysis_share_calibration_and_archive_only_dependents(rig):
    directory = workflow.run(rig.params)
    original_parameters = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    full = analysis.analyze(directory)
    assert full["success"]
    assert full["K_f_Hz_per_mA"] == pytest.approx(1000, rel=1e-4)
    with np.load(directory / "results/calibration_results.npz") as saved:
        original_arrays = {key: saved[key].copy() for key in saved.files}
    ctx = analysis.AnalysisWorkflow(directory)
    assert ctx.states["B04"]["status"] == "completed"
    ctx.execute(["B01", "B02", "B03", "B04"])
    split = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert set(saved.files) == set(original_arrays)
        for key, values in original_arrays.items():
            np.testing.assert_allclose(saved[key], values, rtol=0, atol=0, equal_nan=True, err_msg=key)
    assert split["K_B_nT_per_mA"] == full["K_B_nT_per_mA"]
    curves = (directory / "results/curve_fits.json").read_bytes()
    archived_curves = set((directory / "results").glob("before_*/curve_fits.json"))
    ctx = analysis.AnalysisWorkflow(directory, {"GYROMAGNETIC_RATIO_HZ_PER_NT": 5})
    assert ctx.states["B02"]["status"] == "completed"
    assert ctx.states["B03"]["status"] == "stale"
    ctx.execute(["B03", "B04"])
    assert (directory / "results/curve_fits.json").read_bytes() == curves
    assert ctx.summary["K_B_nT_per_mA"] == pytest.approx(200, rel=1e-4)
    assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_parameters
    assert list((directory / "results").glob("before_*/calibration_evaluation.json"))
    assert set((directory / "results").glob("before_*/curve_fits.json")) == archived_curves


def test_history_without_index_and_raw_version_is_supported_but_not_unknown_identity(rig):
    directory = workflow.run(rig.params)
    (directory / "raw/main_field_scan_index.npz").unlink()
    for summary in (directory / "raw").glob("current_*/frequency_scan.npz"):
        with np.load(summary) as source:
            payload = {key: source[key] for key in source.files}
        payload["r_scalar_mean_v"] = payload.pop("r_mean_v")
        np.savez(summary, **payload)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config.pop("raw_format_version")
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert analysis.analyze(directory)["success"]
    assert "raw_format_version" not in yaml.safe_load(path.read_text(encoding="utf-8"))
    with np.load(directory / "raw/current_000/frequency_scan.npz") as saved:
        assert "r_scalar_mean_v" in saved and "r_mean_v" not in saved
    config["raw_format_version"] = 99
    assert "不支持" in run_format.analysis_block_reason(config)
    config.pop("raw_format_version")
    config.pop("experiment_id")
    assert run_format.analysis_block_reason(config) == ""
    config.pop("data_type")
    assert "无法识别" in run_format.analysis_block_reason(config)


def test_missing_raw_and_modified_stage_artifacts_cannot_be_reused(rig):
    directory = workflow.run(rig.params)
    ctx = analysis.AnalysisWorkflow(directory)
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    ctx.execute(["B01", "B02"])
    (directory / "results/curve_fits.json").write_text("[]", encoding="utf-8")
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B02"]["status"] == "stale"
    with pytest.raises(ValueError, match="缺少前置"):
        renewed.execute(["B03"])
    (directory / "raw/current_000/frequency_scan.npz").unlink()
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale"
    with pytest.raises(ValueError, match="无效的原始数据路径"):
        renewed.execute(["B01"])
    assert renewed.states["B01"]["status"] == "failed"


def test_quality_failure_still_writes_diagnostics_and_original_nan_arrays(rig):
    directory = workflow.run(rig.params)
    for path in (directory / "raw").glob("current_*/frequency_scan.npz"):
        with np.load(path) as source:
            payload = {key: source[key] for key in source.files}
        payload["r_mean_v"][:] = np.nan
        np.savez(path, **payload)
    report = analysis.analyze(directory)
    assert not report["success"] and report["rejection_reasons"]
    assert (directory / "results/main_field_frequency_calibration.png").is_file()
    with np.load(directory / "results/calibration_results.npz") as saved:
        assert not saved["calibration_success"]
        assert np.isnan(saved["K_f_Hz_per_mA"])
    assert analysis.AnalysisWorkflow(directory).snapshot()["summary"]["status"] == "completed"


def test_model_old_wait_migration_and_visible_parameter_coverage():
    old = MxMainFieldCalibrationParams.from_external({"SETTLE_TIME_S": 0.4})
    explicit = MxMainFieldCalibrationParams.from_external({"SETTLE_TIME_S": 0.4, "SYSTEM_SETTLE_S": 0.2})
    assert old.system_settle_s == 0.4 and explicit.system_settle_s == 0.2
    assert MxMainFieldCalibrationParams.from_external(old.to_external()) == old
    from .definition import ADAPTER, DEFINITION
    from .stages import definition
    fields = {field["name"] for field in ADAPTER.schema()["fields"]}
    visible = {key for column in ("acquisition", "analysis") for stage in definition()[column] for key in stage["display_parameters"]}
    assert fields == visible
    assert DEFINITION.required_mapping_keys == run_format.REQUIRED_MAPPING_KEYS
    package = sys.modules[__package__]
    assert package.ADAPTER is ADAPTER and package.DEFINITION is DEFINITION
    assert package.MxMainFieldCalibrationParams is MxMainFieldCalibrationParams
    with pytest.raises(AttributeError, match="unknown_export"):
        getattr(package, "unknown_export")


def test_raw_and_config_fingerprints_force_revalidation_and_keep_old_exports(rig):
    from . import analysis_steps
    directory = workflow.run(rig.params)
    analysis.analyze(directory)
    for name in ("_builtin", "_load_params", "_load_scan_index", "_discrete_peak_frequency",
                 "_analyze_curve", "_failed_linear_fit", "_weighted_linear_fit"):
        assert getattr(analysis, name) is getattr(analysis_steps, name)
    path = directory / "raw/current_000/frequency_scan.npz"
    with np.load(path) as source:
        payload = {key: source[key] for key in source.files}
    payload["r_mean_v"] = payload["r_mean_v"] + 0.001
    np.savez(path, **payload)
    ctx = analysis.AnalysisWorkflow(directory)
    assert all(ctx.states[item]["status"] == "stale" for item in ("B01", "B02", "B03", "B04"))
    ctx.execute(["B01", "B02", "B03", "B04"])
    config_path = directory / "experiment_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["parameters"]["GYROMAGNETIC_RATIO_HZ_PER_NT"] = 6
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    renewed = analysis.AnalysisWorkflow(directory)
    assert renewed.states["B01"]["status"] == "stale"


@pytest.mark.parametrize(("override", "error"), [({"GYROMAGNETIC_RATIO_HZ_PER_NT": 0}, ValueError),
    ({"GYROMAGNETIC_RATIO_HZ_PER_NT": float("nan")}, ValueError),
    ({"LINEAR_R_SQUARED_MIN": True}, TypeError), ({"RUN_TAG": "wrong"}, ValueError),
    ({"GYROMAGNETIC_RATIO_HZ_PER_NT": 0.0005}, ValueError),
    ({"GYROMAGNETIC_RATIO_HZ_PER_NT": float("inf")}, ValueError),
    ({"GYROMAGNETIC_RATIO_HZ_PER_NT": True}, TypeError),
    ({"LINEAR_R_SQUARED_MIN": -0.1}, ValueError),
    ({"LINEAR_R_SQUARED_MIN": 1.1}, ValueError),
    ({"LINEAR_R_SQUARED_MIN": float("nan")}, ValueError),
    ({"LINEAR_R_SQUARED_MIN": "0.5"}, TypeError)])
def test_invalid_analysis_overrides_do_not_rewrite_history(rig, override, error):
    directory = workflow.run(rig.params)
    original = (directory / "experiment_config.yaml").read_bytes()
    with pytest.raises(error):
        analysis.AnalysisWorkflow(directory, override)
    assert (directory / "experiment_config.yaml").read_bytes() == original


@pytest.mark.parametrize("fault", ["write", "replace"])
def test_state_save_failure_keeps_original_config_bytes(rig, monkeypatch, fault):
    directory = workflow.run(rig.params)
    ctx = analysis.AnalysisWorkflow(directory)
    before = ctx.config_path.read_bytes()
    dump, replace = yaml.safe_dump, Path.replace
    failure, destinations = OSError("分析状态落盘失败"), []

    def partial_dump(config, stream=None, **kwargs):
        if fault == "write" and stream is not None:
            destinations.append(Path(stream.name))
            stream.write("analysis_status: partial\n")
            raise failure
        return dump(config, stream, **kwargs)

    def fail_replace(path, target):
        if fault == "replace" and Path(target) == ctx.config_path:
            destinations.append(path)
            raise failure
        return replace(path, target)

    monkeypatch.setattr(yaml, "safe_dump", partial_dump)
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError) as caught:
        ctx.execute(["B01"])
    assert caught.value is failure
    assert ctx.config_path.read_bytes() == before
    assert len(destinations) == 1 and destinations[0].parent == directory
    assert json.loads(ctx.state_path.read_text(encoding="utf-8"))["steps"]["B01"]["status"] == "running"
    assert not list(directory.glob(".*.tmp"))


@pytest.mark.parametrize("fault", ["write", "replace"])
def test_analysis_error_survives_secondary_state_save_and_cleanup_errors(rig, monkeypatch, caplog, fault):
    directory = workflow.run(rig.params)
    (directory / "raw/current_000/frequency_scan.npz").unlink()
    ctx = analysis.AnalysisWorkflow(directory)
    dump, replace, unlink = yaml.safe_dump, Path.replace, Path.unlink
    saved_before_failure, temporary = [], []

    def fail_dump(config, stream=None, **kwargs):
        if config.get("analysis_status") == "failed" and stream is not None:
            saved_before_failure.append(ctx.config_path.read_bytes())
            temporary.append(Path(stream.name))
            if fault == "write":
                stream.write("analysis_status: partial\n")
                raise OSError("二次状态写入失败")
        return dump(config, stream, **kwargs)

    def fail_replace(path, target):
        if fault == "replace" and path in temporary:
            raise OSError("二次状态替换失败")
        return replace(path, target)

    def fail_cleanup(path, *args, **kwargs):
        if path in temporary:
            raise OSError("二次临时文件清理失败")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(yaml, "safe_dump", fail_dump)
        patch.setattr(Path, "replace", fail_replace)
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(ValueError, match="无效的原始数据路径") as caught:
            ctx.execute(["B01", "B02"])
        assert len(saved_before_failure) == len(temporary) == 1
        assert ctx.config_path.read_bytes() == saved_before_failure[0]
        assert yaml.safe_load(saved_before_failure[0])["analysis_status"] == "running"
        state = json.loads(ctx.state_path.read_text(encoding="utf-8"))
        assert state["steps"]["B01"] == {"status": "failed", "error": str(caught.value)}
        assert state["steps"]["B02"]["status"] == "pending"
        assert "运行配置临时文件清理失败" in caplog.text
        assert temporary[0].is_file()
    temporary[0].unlink()
    assert not list(directory.glob(".*.tmp"))


@pytest.mark.parametrize("mode", ["explicit", "missing", "nonexistent"])
def test_real_analysis_module_outside_repository_requires_explicit_directory(rig, mode):
    directory = workflow.run(rig.params)
    original = yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))
    repository = Path(__file__).resolve().parents[3]
    with TemporaryDirectory(prefix="p1-2-analysis-cwd-") as temporary:
        cwd = Path(temporary).resolve()
        assert not cwd.is_relative_to(repository)
        decoy = cwd / "data" / run_format.DATA_TYPE / "9999_latest"
        shutil.copytree(directory, decoy)
        before_decoy = {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
        before_run = {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
        env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_") and k != "PYTHONPATH"}
        env.update(MPLBACKEND="Agg", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        missing = cwd / "not-a-run"
        if mode != "missing":
            env["LAB_RUN_DIR"] = str(directory if mode == "explicit" else missing)
        command = [sys.executable, "-X", "utf8", "-m", analysis.__name__]
        started = time.monotonic()
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120)
        print(json.dumps(dict(command=command, cwd=str(cwd), mode=mode,
            lab_environment={k: v for k, v in env.items() if k.startswith("LAB_")},
            elapsed_seconds=time.monotonic() - started, returncode=result.returncode), ensure_ascii=False))
        print(result.stdout, result.stderr)
        assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before_decoy
        assert not missing.exists()
        assert {p: (directory / p).read_bytes() for p in before_run if p.parts[0] == "raw"} == {
            p: data for p, data in before_run.items() if p.parts[0] == "raw"}
        if mode == "explicit":
            assert result.returncode == 0, result.stdout + result.stderr
            report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
            assert report["success"] and report["K_f_Hz_per_mA"] == pytest.approx(1000, rel=1e-4)
            state = json.loads((directory / "analysis_state.json").read_text(encoding="utf-8"))
            assert all(step["status"] == "completed" for step in state["steps"].values())
            assert (directory / "results/calibration_results.npz").is_file()
            assert yaml.safe_load((directory / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original["parameters"]
        else:
            assert {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before_run
            assert result.returncode != 0
            assert ("缺少 LAB_RUN_DIR" if mode == "missing" else "运行目录不存在") in result.stderr


def test_real_registered_adapter_analysis_discards_old_run_context(rig, monkeypatch):
    from lab_workflows.experiments import get_experiment, typed

    directory = workflow.run(rig.params)
    decoy = directory.with_name("9999_latest")
    shutil.copytree(directory, decoy)
    before = {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
    adapter = get_experiment(run_format.EXPERIMENT_ID).analysis_runner.__self__
    assert adapter.analysis_module == analysis.__name__
    monkeypatch.setattr(adapter, "root", rig.root)
    popen, launches = typed.subprocess.Popen, []

    def launch(command, **kwargs):
        # 仅替换临时项目中不存在的解释器路径，保留真实模块、环境与进程协议。
        launches.append((command, kwargs))
        return popen([sys.executable, *command[1:]], **kwargs)

    monkeypatch.setattr(typed.subprocess, "Popen", launch)
    monkeypatch.setenv("LAB_RUN_DIR", str(decoy))
    monkeypatch.setenv("LAB_TYPED_PARAMETERS", "invalid old analysis context")
    result = adapter.analyze(directory, None)
    assert len(launches) == 1
    command, kwargs = launches[0]
    assert command[-2:] == ["-m", analysis.__name__]
    assert kwargs["cwd"] == rig.root and not rig.root.is_relative_to(Path(__file__).resolve().parents[3])
    assert kwargs["env"]["LAB_RUN_DIR"] == str(directory.resolve())
    assert "LAB_TYPED_PARAMETERS" not in kwargs["env"]
    assert result["run_dir"] == str(directory.resolve())
    report = json.loads((directory / "results/analysis.json").read_text(encoding="utf-8"))
    assert report["success"] and report["K_f_Hz_per_mA"] == pytest.approx(1000, rel=1e-4)
    assert str(directory / "results/calibration_results.npz") in result["artifacts"]
    assert {p.relative_to(decoy): p.read_bytes() for p in decoy.rglob("*") if p.is_file()} == before
    assert not list((rig.root / "data/.gui_jobs").glob("*.cancel"))


@pytest.mark.parametrize("bound", ["minimum", "maximum"])
def test_analysis_overrides_keep_model_bounds_without_hardware_revalidation(rig, monkeypatch, bound):
    from dataclasses import fields
    from .stages import ANALYSIS_KEYS

    directory = workflow.run(rig.params)
    path = directory / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["parameters"]["FIXED_PARAMS.temperature"] = 1e9
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    overrides = {item.metadata["external_name"]: item.metadata[bound] for item in fields(MxMainFieldCalibrationParams)
        if item.metadata["external_name"] in ANALYSIS_KEYS and item.metadata.get(bound) is not None}
    acquisition_params = MxMainFieldCalibrationParams.from_external(config["parameters"] | overrides)
    assert any("temperature" in error for error in acquisition_params.validate(rig.root))
    raw = {p: p.read_bytes() for p in (directory / "raw").rglob("*") if p.is_file()}
    # 不提供项目配置的仓库外 cwd；生产分析不能通过切 cwd 或复制配置绕过边界。
    with TemporaryDirectory(prefix="p1-2-validation-cwd-") as cwd, monkeypatch.context() as patch:
        patch.chdir(cwd)
        ctx = analysis.AnalysisWorkflow(directory, overrides)
        ctx.execute(["B01"])
    assert ctx.states["B01"]["status"] == "completed"
    assert all(ctx.options[key] == value for key, value in overrides.items())
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["parameters"] == config["parameters"]
    assert {p: p.read_bytes() for p in raw} == raw
