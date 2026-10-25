"""DirectAW 真实离线算法、分步等价、历史只读与明确目录入口自检。"""
from hashlib import sha256
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from .test_direct_aw_migration import MODULES

REGRESSION_CASES = {
    "rf-sensitivity-direct-aw-frequency": {"analysis": {
        "sources": ["lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/analysis.py",
                    "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/run_format.py",
                    "lab_workflows/experiment_modules/rf_sensitivity_direct_aw_frequency/__init__.py"],
        "tests": ["test_full_stepwise_numerical_and_readonly", "test_input_artifact_revision_and_archive", "test_explicit_native_cli_and_identity", "test_input_change_and_quality_failure_block_dependents"],
    }},
    "xy-direct-aw-dc-calibration": {"analysis": {
        "sources": ["lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/analysis.py",
                    "lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/run_format.py",
                    "lab_workflows/experiment_modules/xy_direct_aw_dc_calibration/__init__.py"],
        "tests": ["test_full_stepwise_numerical_and_readonly", "test_input_artifact_revision_and_archive", "test_explicit_native_cli_and_identity", "test_input_change_and_quality_failure_block_dependents"],
    }},
}


def synthetic_run(directory, module):
    raw = directory / "raw"
    raw.mkdir(parents=True)
    identity = module.replace("_", "-")
    config = dict(experiment_id=identity, schema_version=1, completion_status="completed", parameters={"RUN_TAG": "historical"})
    (directory/"experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    if module.startswith("rf_"):
        frequencies = np.arange(1, 6, dtype=float) * 500
        phases = np.linspace(0, 360, 5)
        response = np.array([.1, .3, .5, .3, .1])
        r = response[:, None] * np.ones((1, 5))
        x = r * np.cos(np.deg2rad(phases))[None, :]
        y = r * np.sin(np.deg2rad(phases))[None, :]
        np.savez(raw/"freq_phase_scan.npz", freq_Hz=frequencies, phase_deg=phases,
                 r_matrix_V=r, x_matrix_V=x, y_matrix_V=y, theta_matrix_deg=np.zeros_like(r))
    else:
        records=[]
        frequencies = np.linspace(200, 8000, 32)
        for i, voltage in enumerate((-.5, -1., -1.5, .5, 1., 1.5)):
            subdir=raw/f"env_{i:02d}"
            subdir.mkdir()
            omega=3000*abs(voltage)+500
            gamma=200.0
            response=.01+50*np.sqrt(gamma**2+frequencies**2)/np.sqrt((gamma**2-frequencies**2+omega**2)**2+4*gamma**2*frequencies**2)
            for j,(frequency,r) in enumerate(zip(frequencies,response)):
                filename=f"zfreq_{j:04d}.npz"
                np.savez(subdir/filename,xy_envelope_voltage_V=voltage,z_freq_Hz=frequency,demod3_r_vector_mean_V=r)
                records.append(dict(xy_envelope_voltage_V=voltage,z_freq_Hz=float(frequency),subdir=subdir.name,file=filename))
        (raw/"frequency_index.json").write_text(json.dumps(records),encoding="utf-8")
    return directory


def frozen(directory):
    return {p.relative_to(directory).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in [directory/"experiment_config.yaml",*(directory/"raw").rglob("*")] if p.is_file()}


@pytest.mark.parametrize("module", MODULES)
def test_full_stepwise_numerical_and_readonly(tmp_path, module):
    api=importlib.import_module(f"lab_workflows.experiment_modules.{module}.analysis")
    directory=synthetic_run(tmp_path/"legacy",module)
    before=frozen(directory)
    result=api.analyze(directory)
    if module.startswith("rf_"):
        np.testing.assert_allclose(result["freq_response"]["R_V_median"],[.1,.3,.5,.3,.1])
        assert result["primary_peak_response"]["freq_Hz"]==1500
    else:
        fit=result["calibration_models"]["absolute_voltage_primary"]
        assert fit["success"] and fit["slope_Hz_per_V"]==pytest.approx(3000,rel=.01)
        assert fit["intercept_Hz"]==pytest.approx(500,rel=.05)
    ctx=api.AnalysisWorkflow(directory)
    ctx.execute(["B01"])
    actual=api.AnalysisWorkflow(directory).execute(["B02"])
    if module.startswith("rf_"):
        assert actual["freq_response"]==result["freq_response"]
    else:
        assert actual["calibration_models"]==result["calibration_models"]
    assert frozen(directory)==before
    assert all(Path(p).stat().st_size for p in actual["artifacts"])


@pytest.mark.parametrize("module", MODULES)
def test_input_artifact_revision_and_archive(tmp_path, module):
    api=importlib.import_module(f"lab_workflows.experiment_modules.{module}.analysis")
    directory=synthetic_run(tmp_path/"legacy",module)
    ctx=api.AnalysisWorkflow(directory)
    with pytest.raises(ValueError,match="缺少前置"): ctx.execute(["B02"])
    ctx.execute(["B01"])
    manifest=directory/"results/input_manifest.json"
    before=manifest.read_bytes()
    ctx=api.AnalysisWorkflow(directory); ctx.execute(["B02"])
    ctx=api.AnalysisWorkflow(directory); ctx.execute(["B02"])
    assert manifest.read_bytes()==before and list((directory/"results").glob("before_*/analysis.yaml"))
    artifact=next(p for p in (directory/"results").glob("*.png"))
    artifact.write_bytes(b"changed")
    ctx=api.AnalysisWorkflow(directory)
    assert ctx.states["B01"]["status"]=="completed" and ctx.states["B02"]["status"]=="stale"
    (directory/"experiment_config.yaml").write_text("experiment_id: wrong\n",encoding="utf-8")
    with pytest.raises(ValueError,match="身份"): api.AnalysisWorkflow(directory)


@pytest.mark.parametrize("module", MODULES)
def test_explicit_native_cli_and_identity(tmp_path, module):
    api=importlib.import_module(f"lab_workflows.experiment_modules.{module}.analysis")
    directory=synthetic_run(tmp_path/"legacy",module)
    before=frozen(directory)
    outside=tmp_path/"outside";outside.mkdir()
    env=os.environ | {"LAB_RUN_DIR":str(directory),"MPLBACKEND":"Agg"}
    env.pop("LAB_CANCEL_FILE",None);env.pop("LAB_TYPED_PARAMETERS",None)
    command=[sys.executable,"-X","utf8","-m",f"lab_workflows.experiment_modules.{module}.analysis"]
    completed=subprocess.run(command,cwd=outside,env=env,capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert (directory/"results/analysis.yaml").is_file() and frozen(directory)==before
    ctx=api.AnalysisWorkflow(directory)
    assert all(s["status"]=="completed" for s in ctx.states.values())
    with pytest.raises(ValueError,match="可覆盖"): api.AnalysisWorkflow(directory,{"unknown":1})
    config=yaml.safe_load((directory/"experiment_config.yaml").read_text(encoding="utf-8"))
    config["completion_status"]="running"
    (directory/"experiment_config.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises(ValueError,match="采集"): api.AnalysisWorkflow(directory)


@pytest.mark.parametrize("module", MODULES)
def test_input_change_and_quality_failure_block_dependents(tmp_path, module):
    api=importlib.import_module(f"lab_workflows.experiment_modules.{module}.analysis")
    directory=synthetic_run(tmp_path/"legacy",module)
    api.AnalysisWorkflow(directory).execute(["B01"])
    revision=api.AnalysisWorkflow(directory).revision
    config_path=directory/"experiment_config.yaml"
    config=yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config.update(analysis_status="completed",analysis_error=None)
    config_path.write_text(yaml.safe_dump(config),encoding="utf-8")
    assert api.AnalysisWorkflow(directory).states["B01"]["status"]=="completed", "分析状态写回不是科学输入变化"
    if module.startswith("rf_"):
        path=directory/"raw/freq_phase_scan.npz"
        with np.load(path) as data: payload={key:data[key].copy() for key in data.files}
        payload["r_matrix_V"][:]=np.nan
        np.savez(path,**payload)
    else:
        index=directory/"raw/frequency_index.json"
        index.write_text("[]",encoding="utf-8")
    ctx=api.AnalysisWorkflow(directory)
    assert ctx.revision>revision and ctx.states["B01"]["status"]=="stale"
    with pytest.raises(ValueError): ctx.execute(["B01"])
    assert api.AnalysisWorkflow(directory).states["B01"]["status"]=="quality_failed"
    with pytest.raises(ValueError,match="缺少前置"): api.AnalysisWorkflow(directory).execute(["B02"])
