"""离线分析分步会话、质量阻断、指纹失效与历史隔离的回归。"""
from pathlib import Path
import os
import shutil

import numpy as np
import pytest
import yaml

from . import run_format
from .analysis import AnalysisWorkflow, analyze
from .analysis_steps import AnalysisQualityError
from .models import MxYRFFrequencyResponseParams
from .stages import ANALYSIS, definition

F0_HZ = 10000.0
GAMMA_HZ = 300.0
SATURATION_PER_VPP2 = 20000.0


def build_run(root, *, amplitudes=(0.01, 0.02, 0.03), points=41, noise=1e-4, seed=7,
              status="completed", drop_summary=False, extra_config=None):
    run_dir = root / "data" / run_format.DATA_TYPE / "0901_0000_mx_y_rf_freq_resp"
    run_dir.mkdir(parents=True)
    params = MxYRFFrequencyResponseParams(
        rf_amplitude_start_vpp=amplitudes[0], rf_amplitude_stop_vpp=amplitudes[-1],
        rf_amplitude_points=len(amplitudes), frequency_points=points,
    )
    frequency = np.linspace(8000.0, 12000.0, points)
    rng = np.random.default_rng(seed)
    detuning = (frequency - F0_HZ) / GAMMA_HZ
    r_mean = np.stack([
        0.1 + 0.05 * np.sqrt(1 + detuning**2) / (1 + detuning**2 + SATURATION_PER_VPP2 * amp**2)
        + rng.normal(0, noise, frequency.size)
        for amp in amplitudes
    ])
    r_std = np.full_like(r_mean, 1e-5)
    raw = run_dir / "raw"
    raw.mkdir()
    if not drop_summary:
        np.savez(
            raw / "frequency_response.npz",
            frequency_hz=frequency,
            amplitude_vpp=np.asarray(amplitudes),
            r_mean_v=r_mean,
            r_scalar_mean_v=r_mean,
            r_std_v=r_std,
            accepted_attempt_index=np.zeros_like(r_mean, dtype=np.int64),
            actual_rate_sa_s=np.float64(1000.0),
        )
    config = {
        "experiment_id": run_format.EXPERIMENT_ID,
        "data_type": run_format.DATA_TYPE,
        "execution_mode": "typed_workflow",
        "completion_status": status,
        "schema_version": params.schema_version,
        "parameters": params.to_external(),
        "raw_format_version": run_format.RAW_FORMAT_VERSION,
    }
    config.update(extra_config or {})
    (run_dir / "experiment_config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return run_dir


def test_full_analysis_recovers_bloch_parameters_and_reports_split(tmp_path):
    run_dir = build_run(tmp_path)
    raw_bytes = (run_dir / "raw/frequency_response.npz").read_bytes()
    original_parameters = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    result = analyze(run_dir)
    assert result["success"] is True
    per_amplitude = result["per_amplitude"]
    assert [entry["amplitude_vpp"] for entry in per_amplitude] == [0.01, 0.02, 0.03]
    for index, entry in enumerate(per_amplitude):
        assert entry["success"], entry["rejection_reasons"]
        parameters = entry["parameters"]
        assert abs(parameters["center_hz"] - F0_HZ) < 0.05 * GAMMA_HZ
        assert abs(parameters["gamma_hz"] - GAMMA_HZ) < 0.15 * GAMMA_HZ
        expected_saturation = SATURATION_PER_VPP2 * entry["amplitude_vpp"] ** 2
        assert abs(parameters["saturation"] - expected_saturation) < 0.35 * expected_saturation
        assert entry["split"] is (expected_saturation > 1.0)
    scaling = result["saturation_scaling"]
    assert scaling["success"] and scaling["n_points"] == 3
    # S 与 Vpp^2 的线性趋势保留；弱驱动点的 S 估计存在简并偏差，只约束数量级。
    assert 0.4 * SATURATION_PER_VPP2 < scaling["slope_per_vpp2"] < 1.6 * SATURATION_PER_VPP2
    assert scaling["r_squared"] > 0.9
    for name in ("bloch_fit.npz", "fit_diagnostics.yaml", "frequency_response.png",
                 "s_vs_amplitude.png", "analysis.yaml", "analysis.json"):
        assert (run_dir / "results" / name).is_file(), name
    state = AnalysisWorkflow(run_dir).snapshot()
    assert state["steps"]["B03"]["status"] == "completed"
    assert (run_dir / "raw/frequency_response.npz").read_bytes() == raw_bytes
    assert yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == original_parameters
    assert state["summary"]["status"] == "completed"


def test_single_amplitude_skips_saturation_plot(tmp_path):
    run_dir = build_run(tmp_path, amplitudes=(0.01,))
    result = analyze(run_dir)
    assert result["success"] and len(result["per_amplitude"]) == 1
    assert (run_dir / "results/frequency_response.png").is_file()
    assert not (run_dir / "results/s_vs_amplitude.png").exists()
    assert result["saturation_scaling"]["success"] is False


def test_missing_dependency_missing_summary_and_running_directory_are_rejected(tmp_path):
    run_dir = build_run(tmp_path, drop_summary=True)
    ctx = AnalysisWorkflow(run_dir)
    with pytest.raises(FileNotFoundError, match="frequency_response.npz"):
        ctx.execute(["B01"])
    missing = build_run(tmp_path.parent / "other", status="running")
    with pytest.raises(ValueError, match="仍在执行"):
        AnalysisWorkflow(missing)
    third = build_run(tmp_path.parent / "third", extra_config={"experiment_id": "another-experiment"})
    with pytest.raises(ValueError, match="其他实验"):
        AnalysisWorkflow(third)


def test_quality_failure_keeps_diagnostics_and_blocks_report(tmp_path):
    run_dir = build_run(tmp_path, noise=0.02, seed=11)
    ctx = AnalysisWorkflow(run_dir)
    with pytest.raises(AnalysisQualityError, match="没有任何幅度曲线"):
        ctx.execute(["B01", "B02"])
    assert ctx.states["B02"]["status"] == "quality_failed"
    assert ctx.states["B03"]["status"] == "pending"
    assert ctx.snapshot()["summary"]["valid_curve_count"] == 0
    assert all(not curve["success"] for curve in ctx.snapshot()["summary"]["per_amplitude"])
    failed = ctx.states["B02"]
    assert failed["artifacts"] == ["bloch_fit.npz", "fit_diagnostics.yaml"]
    assert failed["input_fingerprint"] == ctx.fingerprint
    assert failed["parameters"] == ctx.options and failed["revision"] == ctx.revision
    assert failed["files"] == {name: ctx._file_signature(run_dir / "results" / name) for name in failed["artifacts"]}
    diagnostics = yaml.safe_load((run_dir / "results/fit_diagnostics.yaml").read_text(encoding="utf-8"))
    assert diagnostics["valid_curve_count"] == 0
    assert (run_dir / "results/bloch_fit.npz").is_file()
    assert not (run_dir / "results/analysis.yaml").exists()
    assert not (run_dir / "results/frequency_response.png").exists()
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])


def test_raw_change_invalidates_and_archive_preserves_previous_artifacts(tmp_path):
    run_dir = build_run(tmp_path)
    analyze(run_dir)
    first_json = (run_dir / "results/analysis.json").read_bytes()
    ctx = AnalysisWorkflow(run_dir)
    assert ctx.revision >= 0 and all(state["status"] == "completed" for state in ctx.states.values())
    # 原始数据内容变化使全部阶段失效。
    with np.load(run_dir / "raw/frequency_response.npz") as data:
        payload = {key: data[key] for key in data.files}
    payload["r_mean_v"] = payload["r_mean_v"] * 1.5
    np.savez(run_dir / "raw/frequency_response.npz", **payload)
    refreshed = AnalysisWorkflow(run_dir)
    assert refreshed.states["B01"]["status"] == "stale"
    refreshed.execute(["B01", "B02", "B03"])
    assert (run_dir / "results/analysis.json").read_bytes() != first_json
    backups = list((run_dir / "results").glob("before_*"))
    assert backups, "重算前必须归档旧产物"
    assert any(path.name == "analysis.json" for path in backups[0].iterdir())


def test_analysis_overrides_invalidate_refit_and_never_touch_acquisition_snapshot(tmp_path):
    run_dir = build_run(tmp_path)
    analyze(run_dir)
    snapshot = yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"]
    ctx = AnalysisWorkflow(run_dir, {"FIT_R_SQUARED_MIN": 0.2, "R_BAD_POINT_STD_THRESHOLD_V": 0.05})
    assert ctx.states["B02"]["status"] == "stale"
    assert ctx.options["FIT_R_SQUARED_MIN"] == 0.2
    with pytest.raises(ValueError, match="不支持的分析覆盖参数"):
        AnalysisWorkflow(run_dir, {"UNKNOWN_KEY": 1})
    ctx.execute(["B02", "B03"])
    state = AnalysisWorkflow(run_dir)
    assert state.options["FIT_R_SQUARED_MIN"] == 0.2
    assert yaml.safe_load((run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))["parameters"] == snapshot
    # 未改变的覆盖重开不再失效。
    assert all(entry["status"] == "completed" for entry in state.states.values())


def test_revision_guard_and_partial_rerun_use_cached_fit(tmp_path):
    run_dir = build_run(tmp_path)
    ctx = AnalysisWorkflow(run_dir)
    ctx.execute(["B01", "B02"])
    fit_bytes = (run_dir / "results/bloch_fit.npz").read_bytes()
    with pytest.raises(ValueError, match="分析修订已改变"):
        ctx.execute(["B03"], expected_revision=ctx.revision - 1)
    ctx.execute(["B03"])
    assert (run_dir / "results/analysis.yaml").is_file()
    # B03 只重绘报告，不重算拟合缓存。
    (run_dir / "results/analysis.json").unlink()
    again = AnalysisWorkflow(run_dir)
    assert again.states["B03"]["status"] == "stale"
    again.execute(["B03"])
    assert (run_dir / "results/bloch_fit.npz").read_bytes() == fit_bytes


def test_stage_definition_declares_real_quality_gates_and_analysis_keys():
    flow = definition()
    gates = {item["id"]: list(item["quality_gates"]) for item in flow["acquisition"] + flow["analysis"]}
    assert gates["A01"] == ["参数、映射、锁相采集能力与安全限值全部有效"]
    assert gates["A03"] == ["参考时钟严格回读一致"]
    assert gates["A08"] == ["每个 (幅度, 频率) 点 std(R) 合格；重采用尽后保存诊断并阻断后继"]
    assert gates["B01"] == ["运行身份、独立原始格式、递增扫描轴与 R 矩阵有效；至少一条曲线有 6 个有效点，目录已停止写入"]
    assert gates["B02"] == [
        "至少一条幅度曲线通过 R²、γ 相对不确定度（有限时）与 γ 小于扫描跨度一半的门槛"]
    assert {item["id"] for item in flow["acquisition"] + flow["analysis"] if item["quality_gates"]} == {
        "A01", "A03", "A08", "B01", "B02"}
    assert [stage.id for stage in ANALYSIS] == ["B01", "B02", "B03"]
    analysis_keys = {key for stage in ANALYSIS for key in stage.parameters}
    assert analysis_keys == {"FIT_R_SQUARED_MIN", "FIT_RELATIVE_GAMMA_UNCERTAINTY_MAX",
                             "R_BAD_POINT_STD_THRESHOLD_V"}


@pytest.mark.parametrize("schema, marker", [(1, None), (2, None), (79, 1)])
def test_verified_history_and_raw_format_are_independent_of_parameter_schema(tmp_path, schema, marker):
    run_dir = build_run(tmp_path, amplitudes=(0.01,), extra_config={"schema_version": schema})
    path = run_dir / "experiment_config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if marker is None:
        config.pop("raw_format_version")
    config["experiment_type"] = config.pop("data_type")
    config.pop("experiment_id")
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    ctx = AnalysisWorkflow(run_dir)
    ctx.execute(["B01"])
    assert ctx.states["B01"]["status"] == "completed"


@pytest.mark.parametrize("changes", [
    {"raw_format_version": 99}, {"raw_format_version": True},
    {"experiment_id": "another"}, {"data_type": "Other"},
    {"parameters": []}, {"raw_format_version": None, "schema_version": 79},
])
def test_config_qualification_rejects_before_any_scientific_archive(tmp_path, changes):
    run_dir = build_run(tmp_path, extra_config=changes)
    results = run_dir / "results"
    results.mkdir()
    output = results / "analysis.json"
    output.write_bytes(b"previous scientific report")
    with pytest.raises(ValueError):
        AnalysisWorkflow(run_dir)
    assert output.read_bytes() == b"previous scientific report"
    assert not list(results.glob("before_*"))


@pytest.mark.parametrize("damage", ["frequency_rank", "frequency_nan", "frequency_duplicate",
    "amplitude_rank", "std_shape", "std_negative", "all_nan", "all_excluded", "broken_npz"])
def test_input_qualification_happens_before_archiving_old_outputs(tmp_path, damage):
    run_dir = build_run(tmp_path)
    path = run_dir / "raw/frequency_response.npz"
    with np.load(path) as data:
        payload = {key: data[key] for key in data.files}
    if damage == "frequency_rank":
        payload["frequency_hz"] = payload["frequency_hz"][None, :]
    elif damage == "frequency_nan":
        payload["frequency_hz"][0] = np.nan
    elif damage == "frequency_duplicate":
        payload["frequency_hz"][1] = payload["frequency_hz"][0]
    elif damage == "amplitude_rank":
        payload["amplitude_vpp"] = np.asarray(0.01)
    elif damage == "std_shape":
        payload["r_std_v"] = payload["r_std_v"][:, :-1]
    elif damage == "std_negative":
        payload["r_std_v"][0, 0] = -1
    elif damage == "all_nan":
        payload["r_mean_v"][:] = np.nan
    elif damage == "all_excluded":
        payload["r_std_v"][:] = 0.1
    np.savez(path, **payload)
    if damage == "broken_npz":
        path.write_bytes(b"not an npz")
    results = run_dir / "results"
    results.mkdir()
    outputs = {name: f"old {name}".encode() for name in ("analysis.json", "bloch_fit.npz", "frequency_response.png")}
    for name, content in outputs.items():
        (results / name).write_bytes(content)
    ctx = AnalysisWorkflow(run_dir)
    with pytest.raises(ValueError):
        ctx.execute(["B01"])
    assert ctx.states["B01"]["status"] == "failed"
    assert {name: (results / name).read_bytes() for name in outputs} == outputs
    assert not list(results.glob("before_*"))


def test_content_fingerprints_detect_same_stat_changes_and_live_context_drift(tmp_path):
    run_dir = build_run(tmp_path, amplitudes=(0.01,))
    analyze(run_dir)
    image = run_dir / "results/frequency_response.png"
    stamp, content = image.stat(), image.read_bytes()
    image.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    os.utime(image, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    refreshed = AnalysisWorkflow(run_dir)
    assert refreshed.states["B02"]["status"] == "completed"
    assert refreshed.states["B03"]["status"] == "stale"
    raw = run_dir / "raw/frequency_response.npz"
    stamp = raw.stat()
    with np.load(raw) as data:
        payload = {key: data[key] for key in data.files}
    payload["r_mean_v"] *= 1.01
    np.savez(raw, **payload)
    assert raw.stat().st_size == stamp.st_size
    os.utime(raw, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="缺少前置"):
        refreshed.execute(["B02"])
    assert refreshed.states["B01"]["status"] == "stale"


def test_complete_and_staged_analysis_produce_identical_scientific_arrays(tmp_path):
    first = build_run(tmp_path / "full", amplitudes=(0.01,), noise=0)
    second = tmp_path / "staged"
    shutil.copytree(first, second)
    analyze(first)
    ctx = AnalysisWorkflow(second)
    for stage in ANALYSIS:
        ctx.execute([stage.id])
    with np.load(first / "results/bloch_fit.npz") as full, np.load(second / "results/bloch_fit.npz") as staged:
        assert full.files == staged.files
        for key in full.files:
            np.testing.assert_allclose(full[key], staged[key], rtol=1e-12, atol=1e-12, equal_nan=True)
    assert ctx.states["B02"]["input_fingerprint"] == ctx.fingerprint
    assert ctx.states["B02"]["parameters"] == ctx.options
