"""原算法冻结基准、完整/分步一致、旧格式和只读原始输入回归。"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps, run_format, stages
from .fitting import fit_one_shot
from .test_workflow import small_params, signal, ROOT

REGRESSION_CASES = {"bb-power-scan-burst": {"analysis": {
    "sources": ["lab_workflows/experiment_modules/bb_power_scan_burst/analysis.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/analysis_steps.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/fitting.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/analysis_plotting.py",
        "lab_workflows/experiment_modules/bb_power_scan_burst/run_format.py", "experiments/BB_Rpump_PowerScan_Burst_plot.py"],
    "tests": ["test_frozen_original_fit_arrays_and_scalars", "test_complete_stepwise_and_archive_preserve_inputs",
        "test_legacy_format_and_failure_diagnostics", "test_explicit_subprocess_entry"]}}}


def synthetic_run(path, *, legacy=False):
    path=Path(path); (path/"raw").mkdir(parents=True)
    p=small_params()
    config=run_format.initial_metadata(p) | {"parameters":p.to_external(),"schema_version":p.schema_version,
        "run_tag":"synthetic","timestamp":"0527_1200", "completion_status":"completed"}
    if legacy:
        for key in ("raw_format_version","experiment_id","parameters","schema_version","completion_status"):
            config.pop(key,None)
    (path/"experiment_config.yaml").write_text(yaml.safe_dump(config,allow_unicode=True),encoding="utf-8")
    t,y=signal()
    for i,v in enumerate(p.pump_power):
        np.savez(path/f"raw/shot_p{i:02d}_avg.npz",time_s=t,signal=y,pump_power_voltage=v,
            t_burst_start_s=0.0,t_burst_end_s=p.burst_duration_s,acq_repeats=p.acq_repeats,
            sample_rate_actual_Hz=500000,points_actual=len(t))
    return path


def frozen_inputs(run):
    return {p.relative_to(run).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [run/"experiment_config.yaml",*(run/"raw").glob("*")] if p.is_file()}


def test_frozen_original_fit_arrays_and_scalars(tmp_path):
    run=synthetic_run(tmp_path/"run")
    result=fit_one_shot(run/"raw/shot_p00_avg.npz",.12,.02)
    expected=json.loads(Path(__file__).with_name("fit_reference.json").read_text(encoding="utf-8"))
    for key,value in expected.items():
        actual=result[key]
        if actual is None:
            assert value is None,key
        else:
            if isinstance(actual,np.ndarray) and actual.shape==signal()[0].shape: actual=actual[::313]
            np.testing.assert_allclose(actual,np.asarray(value,dtype=float) if isinstance(value,list) else value,
                rtol=1e-9,atol=1e-12,equal_nan=True,err_msg=key)


def test_complete_stepwise_and_archive_preserve_inputs(tmp_path):
    full=synthetic_run(tmp_path/"full"); split=synthetic_run(tmp_path/"split")
    before=frozen_inputs(split)
    analysis.analyze(full)
    ctx=analysis.AnalysisWorkflow(split)
    with pytest.raises(ValueError,match="缺少前置"): ctx.execute(["B02"])
    for item in ("B01","B02","B03"): ctx.execute([item],ctx.revision)
    with np.load(full/"results/fit_points.npz") as a,np.load(split/"results/fit_points.npz") as b:
        for key in a.files:
            if a[key].dtype.kind in "USb": np.testing.assert_array_equal(a[key],b[key])
            else: np.testing.assert_allclose(a[key],b[key],equal_nan=True)
    original=(split/"results/fit_points.npz").read_bytes()
    ctx.execute(["B03"],ctx.revision)
    assert (split/"results/fit_points.npz").read_bytes()==original
    assert list((split/"results").glob("before_*/*pub.png"))
    assert frozen_inputs(split)==before
    for name in stages.PLOT_NAMES:
        assert (split/f"results/{name}.png").is_file() and (split/f"results/{name}.pdf").is_file()
    old=ctx.revision
    with pytest.raises(ValueError,match="修订"): ctx.execute(["B03"],old-1)
    changed=analysis.AnalysisWorkflow(split,{"POST_DARK_TIME_S":.018})
    assert changed.states["B01"]["status"]=="completed" and changed.states["B02"]["status"]=="stale"
    changed.execute(["B02","B03"],changed.revision)
    assert (split/"results/input_manifest.json").is_file() and frozen_inputs(split)==before


def test_legacy_format_and_failure_diagnostics(tmp_path):
    run=synthetic_run(tmp_path/"legacy",legacy=True); before=frozen_inputs(run)
    assert analysis_steps.load_config(run)[1].acq_repeats==2
    assert run_format.parameters(analysis_steps.load_config(run)[0],run).pump_power==[.08,.12]
    # 样本不足以拟合时导出诊断，不插值、补点或伪造拟合质量。
    for path in (run/"raw").glob("*.npz"):
        with np.load(path) as source: values={k:source[k].copy() for k in source.files}
        values["time_s"],values["signal"]=values["time_s"][::100],values["signal"][::100]
        np.savez(path,**values)
    before=frozen_inputs(run)
    result=analysis.analyze(run)
    assert result["scientific_quality"]=="diagnostic_only" and frozen_inputs(run)==before
    value=json.loads((run/"results/analysis_pub.json").read_text(encoding="utf-8"))
    assert all(p["R_pump_eff_per_s"] is None for p in value["per_point"])
    assert (run/"results/residuals_diagnostics_pub.png").is_file()


def test_input_change_invalidates_and_missing_does_not_archive(tmp_path):
    run=synthetic_run(tmp_path/"run"); ctx=analysis.AnalysisWorkflow(run)
    ctx.execute(["B01","B02"])
    original=(run/"results/analysis_pub.json").read_bytes()
    path=run/"raw/shot_p00_avg.npz"; path.write_bytes(b"corrupt")
    with pytest.raises((ValueError,OSError,EOFError)):
        analysis.AnalysisWorkflow(run).execute(["B01"])
    assert (run/"results/analysis_pub.json").read_bytes()==original
    path.unlink()
    snapshot=analysis.AnalysisWorkflow(run).snapshot()
    assert snapshot["steps"]["B01"]["status"]=="stale"
    with pytest.raises(ValueError): analysis.AnalysisWorkflow(run).execute(["B02"])


def test_identity_versions_running_and_invalid_overrides(tmp_path):
    run=synthetic_run(tmp_path/"run"); config=yaml.safe_load((run/"experiment_config.yaml").read_text(encoding="utf-8"))
    for update in ({"raw_format_version":99},{"raw_format_version":True},{"experiment_id":"other"},{"completion_status":"running"}):
        (run/"experiment_config.yaml").write_text(yaml.safe_dump(config|update),encoding="utf-8")
        with pytest.raises(ValueError): analysis.AnalysisWorkflow(run)
    (run/"experiment_config.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    for values in ({"POST_DARK_TIME_S":-1},{"RUN_TAG":"not-an-analysis-option"}):
        with pytest.raises(ValueError): analysis.AnalysisWorkflow(run,values)
    # 参数 schema 的改变不等于 raw 格式改变。
    assert run_format.format_block_reason(config|{"schema_version":999}) is None


def test_explicit_subprocess_entry(tmp_path):
    run=synthetic_run(tmp_path/"run"); before=frozen_inputs(run)
    result=subprocess.run([sys.executable,str(ROOT/"experiments/BB_Rpump_PowerScan_Burst_plot.py"),str(run)],cwd=tmp_path,
        env=os.environ|{"MPLBACKEND":"Agg","PYTHONUTF8":"1"},text=True,encoding="utf-8",capture_output=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert (run/"results/analysis_pub.json").is_file() and (run/"results/rpump_eff_vs_pump_power_pub.png").is_file()
    assert frozen_inputs(run)==before
