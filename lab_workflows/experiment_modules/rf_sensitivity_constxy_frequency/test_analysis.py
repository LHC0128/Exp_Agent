"""显式临时目录的原科学计算、历史资格和阶段产物回归。"""
from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pytest
import yaml
from . import analysis, analysis_steps as steps, run_format, stages
from .analysis_algorithms import extract_drive_response, extract_offline_lockin_response, compute_bandwidth, fit_doublelorentzian
from .models import RFConstXYFrequencyParams
from .test_workflow import small_params, ROOT

REGRESSION_CASES = {"rf-sensitivity-constxy-frequency": {"analysis": {
    "sources": ["lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/analysis.py",
        "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/analysis_steps.py",
        "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/analysis_algorithms.py",
        "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/analysis_calculation.py",
        "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/analysis_plotting.py",
        "lab_workflows/experiment_modules/rf_sensitivity_constxy_frequency/run_format.py",
        "experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py"],
    "tests": ["test_original_fft_lockin_and_bandwidth_rules", "test_complete_and_stepwise_match_and_plots_keep_numeric_inputs",
        "test_legacy_parameters_and_raw_version_independent", "test_invalid_inputs_keep_old_products", "test_real_thin_entry_requires_explicit_directory"]}}}


def synthetic_run(path, *, legacy=False, mode="both"):
    path = Path(path); (path / "raw").mkdir(parents=True)
    params = replace(small_params(), acquisition_mode=mode)
    config = run_format.initial_metadata(params) | {"experiment_type":run_format.DATA_TYPE,"schema_version":2,
        "parameters":params.to_external(),"run_tag":"synthetic","timestamp":"synthetic","completion_status":"completed"}
    if legacy:
        config.pop("parameters"); config.pop("raw_format_version"); config.pop("experiment_id")
        config["hf2_demod0"] = dict(rate_Sa_s_requested=params.hf2_demod0_rate, TC_meas_s=params.hf2_demod0_tc,
            TC_calib_s=params.hf2_demod0_tc_calib, osc_freq_Hz=params.hf2_demod0_osc_freq)
        config["hf2_demod_r"] = dict(demod_idx=3, osc_idx=1, n_avg=params.demod3_n_avg)
        config["xy_carrier_phase_calibration"] = {"enabled":True}
    (path / "experiment_config.yaml").write_text(yaml.safe_dump(config,allow_unicode=True),encoding="utf-8")
    records = []
    t = np.arange(256)/1024
    for i, f in enumerate(params.frequency_axis()):
        name=f"freq_{i:04d}_{f:.3f}Hz.npz"
        data=dict(freq_Hz=f,is_baseline=np.uint8(f==0),acquisition_mode=mode,z_drive_amplitude_Vpp=0.01,
            actual_rate_Sa_s=1024.0,acquisition_complete=True)
        if mode in ("daq_fft_y","both"):
            data.update(time_s=t,y_V=0.02*np.sin(2*np.pi*f*t+0.3)+np.random.default_rng(i).normal(0,1e-6,len(t)))
        if f>0 and mode in ("demod_r","both"):
            data.update(demod_r_r_vector_mean_V=0.01,demod_r_r_mean_scalar_V=0.011,demod_r_r_std_scalar_V=0.0001,
                demod_r_x_mean_V=0.01,demod_r_y_mean_V=0,demod_r_phase_vector_rad=0,demod_r_phase_vector_deg=0)
        np.savez(path/"raw"/name,**data)
        records.append(dict(idx=i,freq_Hz=float(f),is_baseline=bool(f==0),file=name))
    (path/"raw/frequency_index.json").write_text(json.dumps(records),encoding="utf-8")
    np.savez(path/"raw/frequency_index.npz",records=np.array(records,dtype=object))
    return path


def frozen_inputs(path):
    paths = [path/"experiment_config.yaml", *sorted((path/"raw").rglob("*"))]
    return {str(p.relative_to(path)):[p.stat().st_size,p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()]
        for p in paths if p.is_file()}


def test_original_fft_lockin_and_bandwidth_rules():
    fs=1024; t=np.arange(1024)/fs; y=0.02*np.sin(2*np.pi*100*t+0.3)
    fft=extract_drive_response(y,fs,100)
    li=extract_offline_lockin_response(y,fs,100)
    assert fft["amplitude_V"] == pytest.approx(0.02,rel=1e-12)
    assert li["offline_lockin_r_V"] == pytest.approx(0.02,rel=1e-12)
    assert not extract_offline_lockin_response(y[:4],fs,1)["is_valid"]
    bw=compute_bandwidth(np.array([0,1,2,3,4]),np.array([100,0,2,1.5,0]))
    assert bw["peak_freq_Hz"] == 2 and bw["bandwidth_Hz"] == 1
    assert not bw["bandwidth_is_censored"]


def test_double_lorentzian_no_offset_covariance_and_original_model():
    f=np.linspace(100,800,20); g,o,p=25,400,0.4
    y=p*np.sqrt(g*g+f*f)/np.sqrt((g*g-f*f+o*o)**2+4*g*g*f*f)
    result=fit_doublelorentzian(f,y,p0_Gamma=g,p0_Omega=o,p0_P0=p,include_offset=False)
    assert result["success"] and result["offset_V"] == result["offset_unc_V"] == 0
    assert result["Gamma_Hz"] == pytest.approx(g,rel=1e-6)
    assert result["Omega_Ctrl_Hz"] == pytest.approx(o,rel=1e-6)


def test_complete_and_stepwise_match_and_plots_keep_numeric_inputs(tmp_path):
    full=synthetic_run(tmp_path/"full"); step=synthetic_run(tmp_path/"step")
    before=frozen_inputs(full)
    analysis.analyze(full)
    for stage in stages.ANALYSIS:
        analysis.analyze(step,step_ids=[stage.id])
    with np.load(full/"results/frequency_response.npz") as a, np.load(step/"results/frequency_response.npz") as b:
        assert set(a.files) == set(b.files)
        for key in a.files:
            if a[key].dtype.kind in "US": np.testing.assert_array_equal(a[key],b[key])
            else: np.testing.assert_allclose(a[key],b[key],rtol=1e-12,atol=1e-18,equal_nan=True)
        assert a["fft_amp_V"].shape == (2,)
        np.testing.assert_allclose(a["hardware_demod_r_vector_V"],0.01)
        np.testing.assert_allclose(a["hardware_demod_r_scalar_mean_V"],0.011)
    numeric={p:p.read_bytes() for p in (full/"results").glob("*.npz")}
    analysis.analyze(full,step_ids=["B03"])
    assert {p:p.read_bytes() for p in numeric} == numeric
    assert (full/"results/frequency_response_comparison.png").is_file()
    assert (full/"results/frequency_response_comparison.pdf").is_file()
    assert frozen_inputs(full) == before
    assert analysis.AnalysisWorkflow(full).snapshot()["summary"]["status"] == "completed"


@pytest.mark.parametrize("mode", ["daq_fft_y","demod_r"])
def test_single_modes_and_empty_fit_diagnostics(tmp_path, mode):
    run=synthetic_run(tmp_path/mode,mode=mode)
    result=analysis.analyze(run)
    assert result["status"] == "completed"
    report=json.loads((run/"results/analysis.json").read_text(encoding="utf-8"))
    assert report["acquisition_mode"] == mode
    assert not report["lorentzian_daq_fft"].get("success")
    assert (run/"results/plot_manifest.json").is_file()


def test_legacy_parameters_and_raw_version_independent(tmp_path):
    run=synthetic_run(tmp_path/"legacy",legacy=True)
    config,p=steps.load_config(run)
    assert p.z_rf_freq_points == 3 and p.hf2_demod0_rate == 1024
    assert p.system_settle_s == 0.1  # 缺失的旧关温控等待按历史回退值参与最大值迁移。
    assert not run_format.format_block_reason(config)
    assert not run_format.format_block_reason(config | {"schema_version":1,"raw_format_version":1})
    assert run_format.format_block_reason(config | {"raw_format_version":True})
    assert run_format.format_block_reason(config | {"raw_format_version":2})
    assert run_format.format_block_reason({"experiment_type":run_format.DATA_TYPE,"acquisition_mode":"both"})
    (run/"raw/frequency_index.json").unlink()
    assert len(steps.load_inputs(run)) == 3
    before=frozen_inputs(run)
    ctx=analysis.AnalysisWorkflow(run)
    assert frozen_inputs(run) == before
    ctx.execute(["B01","B02"],ctx.revision)
    assert frozen_inputs(run) == before


@pytest.mark.parametrize("failure", ["missing","nan","path","identity","incomplete"])
def test_invalid_inputs_keep_old_products(tmp_path, failure):
    run=synthetic_run(tmp_path/"run"); (run/"results").mkdir()
    old=run/"results/analysis.json"; old.write_text('{"keep":true}',encoding="utf-8")
    path=next((run/"raw").glob("freq_*.npz"))
    if failure == "missing": path.unlink()
    elif failure in ("nan","incomplete"):
        with np.load(path) as f: data={k:f[k].copy() for k in f.files}
        if failure == "nan": data["y_V"][0]=np.nan
        else: data["acquisition_complete"]=False
        np.savez(path,**data)
    elif failure == "path":
        target=run/"raw/frequency_index.json"; records=json.loads(target.read_text(encoding="utf-8")); records[0]["file"]="../escape.npz"
        target.write_text(json.dumps(records),encoding="utf-8")
    else:
        target=run/"experiment_config.yaml"; config=yaml.safe_load(target.read_text(encoding="utf-8")); config["experiment_id"]="wrong"
        target.write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises((ValueError,FileNotFoundError)): analysis.analyze(run)
    assert old.read_text(encoding="utf-8") == '{"keep":true}' and not list((run/"results").glob("before_*"))


def test_content_fingerprints_options_revision_and_directory_isolation(tmp_path):
    run=synthetic_run(tmp_path/"run"); other=synthetic_run(tmp_path/"other")
    analysis.analyze(run,step_ids=["B01","B02"])
    before=frozen_inputs(run)
    ctx=analysis.AnalysisWorkflow(run,{"FIT_MODEL":"single_lorentzian","DLZ_INCLUDE_OFFSET":False})
    assert ctx.states["B01"]["status"] == "completed" and ctx.states["B02"]["status"] == "stale"
    assert analysis.AnalysisWorkflow(other).options["DLZ_INCLUDE_OFFSET"] is True
    ctx.execute(["B02"],ctx.revision)
    assert frozen_inputs(run) == before
    with pytest.raises(ValueError,match="修订"): ctx.execute(["B02"],-1)
    product=run/"results/responses.json"; stamp=product.stat().st_mtime_ns
    product.write_bytes(product.read_bytes().replace(b'0.02',b'0.03',1)); os.utime(product,ns=(stamp,stamp))
    assert analysis.AnalysisWorkflow(run).states["B01"]["status"] == "stale"
    for options in ({"unexpected":1},{"DLZ_GAMMA_MIN_HZ":-1},{"MAX_PICK_FREQ_ERROR_HZ":float("nan")}):
        with pytest.raises(ValueError): analysis.AnalysisWorkflow(run,options)


def test_analysis_cannot_start_while_acquisition_running(tmp_path):
    run=synthetic_run(tmp_path/"run")
    path=run/"experiment_config.yaml"; config=yaml.safe_load(path.read_text(encoding="utf-8")); config["completion_status"]="running"
    path.write_text(yaml.safe_dump(config),encoding="utf-8")
    with pytest.raises(ValueError,match="写入"): analysis.AnalysisWorkflow(run)


def test_real_thin_entry_requires_explicit_directory(tmp_path):
    entry=ROOT/"experiments/RF_Field_Sensitivity_ConstXY_FreqSweep_plot.py"
    run=synthetic_run(tmp_path/"run"); before=frozen_inputs(run)
    result=subprocess.run([sys.executable,"-X","utf8",str(entry),str(run)],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=90)
    assert result.returncode == 0,result.stdout+result.stderr
    assert (run/"results/frequency_response_comparison.png").is_file()
    assert frozen_inputs(run) == before
    result=subprocess.run([sys.executable,str(entry)],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",timeout=30)
    assert result.returncode != 0 and "run_dir" in result.stderr
