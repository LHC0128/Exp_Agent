"""父摘要分析的真实文件、数值、阶段与路径边界回归；不读取真实历史。"""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import yaml

from ...common import WorkflowCancelled
from . import analysis, analysis_plotting, analysis_steps, run_format, stages


REGRESSION_CASES = {
    "mx-y-rf-sensitivity-drift": {
        "analysis": {
            "sources": [
                "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/__init__.py",
                "lab_workflows/experiment_modules/mx_y_rf_sensitivity_drift/analysis.py"
            ],
            "tests": [
                "test_full_and_separate_stages_share_original_numeric_rows_and_preserve_sources",
                "test_real_plot_inputs_keep_elapsed_clock_time_and_all_three_original_trends",
                "test_online_refresh_allows_running_and_zero_valid_without_changing_parent_or_stage_state",
                "test_parent_identity_format_and_running_status_reject_without_writes",
                "test_output_directory_link_cannot_write_raw_or_escape_project",
                "test_b01_rejects_bad_references_and_data_before_archiving_old_results",
                "test_no_valid_cycle_exports_real_diagnostics_and_partial_parent_keeps_original_indices",
                "test_legacy_invalid_nan_stays_excluded_and_csv_nan_becomes_standard_json_null",
                "test_no_parent_options_missing_dependencies_revision_and_snapshot_are_read_only",
                "test_input_sha_invalidates_even_same_size_mtime_and_live_instance",
                "test_output_sha_invalidates_only_affected_stage_even_same_size_mtime",
                "test_b03_and_b02_recompute_archive_only_their_own_and_later_products",
                "test_changed_bad_input_keeps_old_report_before_any_archive",
                "test_report_failure_and_secondary_diagnostic_failure_keep_original_and_b02",
                "test_cancel_before_archive_keeps_outputs_and_blocks_following_stages",
                "test_module_subprocess_uses_explicit_temporary_run_and_generates_real_products",
                "test_read_only_legacy_summary_sanitizes_json_without_claiming_stepwise_analysis"
            ]
        }
    }
}


def yaml_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def json_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_run(root, name="parent", *, statuses=("completed", "completed", "pending"), marked=True):
    parent = root / "data" / run_format.DATA_TYPE / name
    config = dict(experiment_id=run_format.EXPERIMENT_ID, data_type=run_format.DATA_TYPE,
        schema_version=999, parameters={"UNCHANGED_PARAMETER": 17}, completion_status="completed",
        scan_mode="repeated_measurement", child_experiment_id=run_format.CHILD_EXPERIMENT_ID,
        manifest=run_format.MANIFEST)
    if marked:
        config["raw_format_version"] = 1
    yaml_file(parent / "experiment_config.yaml", config)
    records = []
    for index, status in enumerate(statuses):
        child = root / "data" / run_format.CHILD_DATA_TYPE / f"{name}_cycle_{index:03d}"
        record = dict(cycle_index=index, scheduled_offset_s=index * 120.0, status=status,
            child_run_dir=str(child) if status != "pending" else None,
            actual_start_utc=f"2026-08-06T08:{index * 2:02d}:00+00:00" if status != "pending" else None,
            start_lag_s=index * .25 if status != "pending" else None, analysis=None,
            error="R 点质量不合格" if status == "invalid_quality" else None)
        records.append(record)
        if status == "pending":
            continue
        child_config = dict(experiment_id=run_format.CHILD_EXPERIMENT_ID, data_type=run_format.CHILD_DATA_TYPE,
            schema_version=999, parameters={"Y_RF_NT_PER_VPP": 10}, parent_experiment_id=run_format.EXPERIMENT_ID,
            parent_run_dir=str(parent), drift_cycle_index=index, completion_status=status)
        if marked:
            child_config["raw_format_version"] = 1
        yaml_file(child / "experiment_config.yaml", child_config)
        if status == "invalid_quality":
            continue
        result = dict(experiment_id=run_format.CHILD_EXPERIMENT_ID, run_dir=str(child), success=True,
            linewidth_mode="amplitude_equivalent", flat_median_ft_per_sqrt_hz=48.0 + index,
            primary_slope_v_per_vpp=4.0 + index, flat_band_hz=[30.0, 120.0],
            flat_detection=dict(success=True, reason=""), linewidth=dict(hwhm_hz=240.0 + index,
                linewidth_kind="amplitude_equivalent"), response_fit=dict(r_squared=.9 + index * .01, excluded_point_count=index),
            zero_point_method=dict(slope_v_per_vpp=3.0 + index, flat_median_ft_per_sqrt_hz=64.0 + index),
            warnings=["已有子分析诊断"] if index == 0 else [])
        json_file(child / "results/analysis.json", result)
    manifest = dict(schema_version=1, records=records)
    if marked:
        manifest["raw_format_version"] = 1
    yaml_file(parent / run_format.MANIFEST, manifest)
    return parent


def mapping(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def records(parent):
    return mapping(parent / run_format.MANIFEST)["records"]


def child(parent, index=0):
    return Path(records(parent)[index]["child_run_dir"])


def change_yaml(path, update):
    payload = mapping(path)
    update(payload)
    yaml_file(path, payload)


def change_summary(parent, update, index=0):
    path = child(parent, index) / "results/analysis.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    update(payload)
    json_file(path, payload)


def source_snapshot(parent):
    paths = [parent / run_format.MANIFEST]
    paths += [path for record in records(parent) if record["child_run_dir"]
        for path in Path(record["child_run_dir"]).rglob("*") if path.is_file()]
    return {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def test_full_and_separate_stages_share_original_numeric_rows_and_preserve_sources(tmp_path, monkeypatch):
    full, split = make_run(tmp_path, "full"), make_run(tmp_path, "split")
    before = source_snapshot(full) | source_snapshot(split)
    from ..mx_y_rf_sensitivity import analysis as child_analysis
    monkeypatch.setattr(child_analysis, "analyze", lambda *args, **kwargs: pytest.fail("父分析不能重算子科学结果"))
    payload = analysis.analyze(full, project_root=tmp_path)
    for item in ("B01", "B02", "B03"):
        ctx = analysis.AnalysisWorkflow(split, project_root=tmp_path)
        split_payload = ctx.execute([item], ctx.revision)
    assert payload["success"] and split_payload["success"]
    assert payload["cycle_count"] == 3 and payload["valid_cycle_count"] == 2
    left, right = payload["rows"], split_payload["rows"]
    assert [{k: v for k, v in row.items() if k != "child_run_dir"} for row in left] == [
        {k: v for k, v in row.items() if k != "child_run_dir"} for row in right]
    assert [row["actual_elapsed_s"] for row in left] == [0, 120.25, 240]
    assert [row["sensitivity_flat_median_ft_per_sqrt_hz"] for row in left] == [48, 49, None]
    assert [row["zero_point_sensitivity_flat_median_ft_per_sqrt_hz"] for row in left] == [64, 65, None]
    assert [row["linewidth_hwhm_hz"] for row in left] == [240, 241, None]
    assert [row["response_fit_r_squared"] for row in left] == [.9, .91, None]
    assert [row["valid"] for row in left] == [True, True, False]
    with (full / "results/drift_summary.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == analysis.FIELDNAMES
        csv_rows = list(reader)
    assert float(csv_rows[1]["actual_elapsed_s"]) == 120.25
    assert csv_rows[0]["warnings"] == "已有子分析诊断"
    assert source_snapshot(full) | source_snapshot(split) == before
    assert mapping(full / "experiment_config.yaml")["parameters"] == {"UNCHANGED_PARAMETER": 17}
    get_before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in full.rglob("*") if path.is_file()}
    assert analysis.AnalysisWorkflow(full, project_root=tmp_path).snapshot()["options"] == {}
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in full.rglob("*") if path.is_file()} == get_before
    from GUI.backend.workflow_sessions import XYAnalysisState
    snapshot = analysis.AnalysisWorkflow(full, project_root=tmp_path).snapshot()
    validated = XYAnalysisState.model_validate(snapshot)
    assert all(record.input_fingerprint and record.parameters == {} and record.revision == snapshot["revision"]
        for record in validated.steps.values())


def test_real_plot_inputs_keep_elapsed_clock_time_and_all_three_original_trends(tmp_path, monkeypatch):
    parent = make_run(tmp_path)
    figures = {}
    save = analysis_plotting.save_figure
    def inspect(fig, path):
        figures[path.name] = [
            [(line.get_xdata().copy(), line.get_ydata().copy()) for line in axis.lines] for axis in fig.axes]
        assert all(axis.get_ylabel() for axis in fig.axes) and fig.axes[-1].get_xlabel()
        return save(fig, path)
    monkeypatch.setattr(analysis_plotting, "save_figure", inspect)
    analysis.analyze(parent, project_root=tmp_path)
    elapsed = figures["drift_trend.png"]
    np.testing.assert_allclose(elapsed[0][0][0], [0, 120.25 / 3600])
    np.testing.assert_allclose(elapsed[0][0][1], [48, 49])
    np.testing.assert_allclose(elapsed[0][1][1], [64, 65])
    np.testing.assert_allclose(elapsed[1][0][1], [240, 241])
    np.testing.assert_allclose(elapsed[2][0][1], [.9, .91])
    clock = figures["drift_trend_clock_time.png"][0][0][0]
    assert [time.hour for time in clock] == [16, 16]
    assert [time.minute for time in clock] == [0, 2]
    assert all(str(time.tzinfo) == "Asia/Shanghai" for time in clock)
    assert all((parent / "results" / name).stat().st_size > 1000 for name in figures)


def test_online_refresh_allows_running_and_zero_valid_without_changing_parent_or_stage_state(tmp_path):
    parent = make_run(tmp_path, statuses=("pending", "pending"))
    change_yaml(parent / "experiment_config.yaml", lambda config: config.update(completion_status="running"))
    before = (parent / "experiment_config.yaml").read_bytes()
    payload = analysis.refresh_trend(parent)
    assert payload["success"] and payload["valid_cycle_count"] == 0 and payload["status"] == "online"
    assert len(payload["invalid_cycles"]) == 2
    assert (parent / "experiment_config.yaml").read_bytes() == before
    assert not (parent / "analysis_state.json").exists()
    with pytest.raises(ValueError, match="仍在执行"):
        analysis.AnalysisWorkflow(parent, project_root=tmp_path)


@pytest.mark.parametrize("field,value", [("experiment_id", "other"), ("data_type", "Other"),
    ("raw_format_version", True), ("raw_format_version", 2), ("scan_mode", "other"),
    ("completion_status", "running"), ("completion_status", "acquired")])
def test_parent_identity_format_and_running_status_reject_without_writes(tmp_path, field, value):
    parent = make_run(tmp_path)
    change_yaml(parent / "experiment_config.yaml", lambda config: config.update({field: value}))
    before = {path: path.read_bytes() for path in parent.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        analysis.analyze(parent, project_root=tmp_path)
    assert {path: path.read_bytes() for path in parent.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("target_name", ["raw", "outside"])
def test_output_directory_link_cannot_write_raw_or_escape_project(tmp_path, target_name):
    parent = make_run(tmp_path)
    target = parent / "raw" if target_name == "raw" else tmp_path / "outside"
    target.mkdir(exist_ok=True)
    before = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}
    link = parent / "results"
    if os.name == "nt":
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, errors="replace", timeout=10)
        assert command.returncode == 0, command.stdout + command.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="链接|超出"):
            analysis.analyze(parent, project_root=tmp_path)
        assert {path: path.read_bytes() for path in target.rglob("*") if path.is_file()} == before
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("damage", ["escape", "relative_escape", "child_identity", "child_cycle", "child_parent",
    "child_running", "child_format", "summary_identity", "summary_directory", "summary_json", "summary_boolean",
    "flat_boolean", "missing_summary", "missing_manifest", "bad_manifest", "bad_band", "nonfinite", "bad_lag",
    "negative_lag", "duplicate_child", "inline_override", "optional_nan", "invalid_inf"])
def test_b01_rejects_bad_references_and_data_before_archiving_old_results(tmp_path, damage):
    parent = make_run(tmp_path)
    child_dir = child(parent)
    manifest = parent / run_format.MANIFEST
    if damage in ("escape", "relative_escape"):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("不能读取或改写", encoding="utf-8")
        value = str(outside) if damage == "escape" else "../../../outside"
        change_yaml(manifest, lambda p: p["records"][0].update(child_run_dir=value))
    elif damage.startswith("child_"):
        updates = {"child_identity": {"experiment_id": "other"}, "child_cycle": {"drift_cycle_index": 8},
            "child_parent": {"parent_run_dir": "unrelated"}, "child_running": {"completion_status": "running"},
            "child_format": {"raw_format_version": True}}
        change_yaml(child_dir / "experiment_config.yaml", lambda p: p.update(updates[damage]))
    elif damage == "summary_identity":
        change_summary(parent, lambda p: p.update(experiment_id="other"))
    elif damage == "summary_directory":
        change_summary(parent, lambda p: p.update(run_dir="different_child"))
    elif damage == "summary_json":
        (child_dir / "results/analysis.json").write_text("{", encoding="utf-8")
    elif damage == "summary_boolean":
        change_summary(parent, lambda p: p.update(success="true"))
    elif damage == "flat_boolean":
        change_summary(parent, lambda p: p["flat_detection"].update(success="false"))
    elif damage == "missing_summary":
        (child_dir / "results/analysis.json").unlink()
    elif damage == "missing_manifest":
        manifest.unlink()
    elif damage == "bad_manifest":
        change_yaml(manifest, lambda p: p["records"][1].update(cycle_index=0))
    elif damage == "bad_band":
        change_summary(parent, lambda p: p.update(flat_band_hz=[120, 30]))
    elif damage == "nonfinite":
        change_summary(parent, lambda p: p.update(flat_median_ft_per_sqrt_hz=float("nan")))
    elif damage == "optional_nan":
        change_summary(parent, lambda p: p["zero_point_method"].update(slope_v_per_vpp=float("nan")))
    elif damage == "invalid_inf":
        change_summary(parent, lambda p: (p["flat_detection"].update(success=False), p["linewidth"].update(hwhm_hz=float("inf"))))
    elif damage in ("bad_lag", "negative_lag"):
        change_yaml(manifest, lambda p: p["records"][0].update(start_lag_s=float("inf") if damage == "bad_lag" else -1))
    elif damage == "duplicate_child":
        change_yaml(manifest, lambda p: p["records"][1].update(child_run_dir=str(child_dir)))
    elif damage == "inline_override":
        change_yaml(manifest, lambda p: p["records"][0].update(analysis={"success": True}))
    old = parent / "results/analysis.json"
    old.parent.mkdir()
    old.write_bytes(b'{"old_result":true}')
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    with pytest.raises(ValueError):
        ctx.execute(["B01"])
    assert old.read_bytes() == b'{"old_result":true}'
    assert not list(old.parent.glob("before_*"))
    assert ctx.states["B01"]["status"] == "quality_failed"
    assert ctx.summary["error"]
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B02"])


def test_no_valid_cycle_exports_real_diagnostics_and_partial_parent_keeps_original_indices(tmp_path, monkeypatch):
    parent = make_run(tmp_path, statuses=("completed", "invalid_quality", "pending"), marked=False)
    change_yaml(parent / "experiment_config.yaml", lambda p: p.update(completion_status="failed"))
    change_summary(parent, lambda p: p["flat_detection"].update(success=False, reason="没有稳定平坦频段"))
    plots = {}
    save = analysis_plotting.save_figure
    def inspect(fig, path):
        plots[path.name] = dict(line_counts=[len(axis.lines[0].get_ydata()) for axis in fig.axes],
            invalid_points=len(fig.axes[2].collections[0].get_offsets()))
        return save(fig, path)
    monkeypatch.setattr(analysis_plotting, "save_figure", inspect)
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    ctx.execute(["B01", "B02", "B03"])
    assert ctx.summary["success"] and ctx.summary["status"] == "completed"
    assert ctx.summary["valid_cycle_count"] == 0
    assert [row["reason"] for row in ctx.summary["invalid_cycles"]] == ["没有稳定平坦频段", "R 点质量不合格", "尚无子分析摘要"]
    assert all(not row["valid"] for row in ctx.summary["rows"])
    assert all(plot["line_counts"] == [0, 0, 0] for plot in plots.values())
    assert plots["drift_trend.png"]["invalid_points"] == 3
    assert plots["drift_trend_clock_time.png"]["invalid_points"] == 2
    with (parent / "results/drift_summary.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3 and all(row["valid"] == "False" for row in rows)
    assert float(rows[0]["sensitivity_flat_median_ft_per_sqrt_hz"]) == 48
    restored = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    assert restored.summary["status"] == "completed" and restored.summary["valid_cycle_count"] == 0
    change_summary(parent, lambda p: p["flat_detection"].update(success=True))
    change_yaml(parent / run_format.MANIFEST, lambda p: p["records"][2].update(cycle_index=81))
    payload = analysis.analyze(parent, project_root=tmp_path)
    assert [row["cycle_index"] for row in payload["rows"]] == [0, 1, 81]
    assert payload["valid_cycle_count"] == 1
    change_yaml(parent / "experiment_config.yaml", lambda p: p.update(raw_format_version=1))
    with pytest.raises(ValueError, match="连续"):
        analysis.analyze(parent, project_root=tmp_path)


def test_legacy_invalid_nan_stays_excluded_and_csv_nan_becomes_standard_json_null(tmp_path):
    parent = make_run(tmp_path)
    change_summary(parent, lambda p: (p["flat_detection"].update(success=False, reason="旧平坦检测失败"),
        p.update(flat_median_ft_per_sqrt_hz=float("nan"), flat_band_hz=[float("nan"), float("nan")])))
    before = source_snapshot(parent)
    payload = analysis.analyze(parent, project_root=tmp_path)
    assert payload["valid_cycle_count"] == 1 and not payload["rows"][0]["valid"]
    assert payload["rows"][0]["sensitivity_flat_median_ft_per_sqrt_hz"] is None
    assert payload["invalid_cycles"][0]["reason"] == "旧平坦检测失败"
    with (parent / "results/drift_summary.csv").open(encoding="utf-8", newline="") as stream:
        first = next(csv.DictReader(stream))
    assert first["sensitivity_flat_median_ft_per_sqrt_hz"] == first["flat_band_low_hz"] == "nan"
    assert "NaN" not in (parent / "results/analysis.json").read_text(encoding="utf-8")
    assert source_snapshot(parent) == before


def test_no_parent_options_missing_dependencies_revision_and_snapshot_are_read_only(tmp_path):
    parent = make_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    assert stages.ANALYSIS_KEYS == () and stages.definition()["parameter_impacts"]["analysis"] == {}
    assert all(not stage.parameters and stage.quality_gates for stage in stages.ANALYSIS)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()}
    for selected in ([], ["B02"], ["B03"], ["B01", "B01"]):
        with pytest.raises(ValueError):
            ctx.execute(selected)
    with pytest.raises(ValueError, match="修订"):
        ctx.execute(["B01"], ctx.revision + 1)
    with pytest.raises(ValueError, match="没有覆盖参数"):
        analysis.AnalysisWorkflow(parent, {"FIT_R_SQUARED_MIN": .5}, project_root=tmp_path)
    ctx.snapshot()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()} == before


def test_input_sha_invalidates_even_same_size_mtime_and_live_instance(tmp_path):
    parent = make_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    ctx.execute(["B01", "B02"])
    source = child(parent) / "results/analysis.json"
    stat = source.stat()
    text = source.read_text(encoding="utf-8")
    source.write_text(text.replace('48.0', '58.0'), encoding="utf-8")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    with pytest.raises(ValueError, match="缺少前置"):
        ctx.execute(["B03"])
    restored = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    assert restored.states["B01"]["status"] == restored.states["B02"]["status"] == "stale"
    payload = restored.execute(["B01", "B02", "B03"])
    assert payload["rows"][0]["sensitivity_flat_median_ft_per_sqrt_hz"] == 58


@pytest.mark.parametrize("artifact,first_stale", [("analysis_inputs.json", "B01"),
    ("drift_summary.json", "B02"), ("drift_summary.csv", "B02"), ("drift_trend.png", "B03"),
    ("drift_trend_clock_time.png", "B03"), ("analysis.json", "B03"), ("analysis.yaml", "B03")])
def test_output_sha_invalidates_only_affected_stage_even_same_size_mtime(tmp_path, artifact, first_stale):
    parent = make_run(tmp_path)
    analysis.analyze(parent, project_root=tmp_path)
    path = parent / "results" / artifact
    stat, data = path.stat(), bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    for stage in stages.ANALYSIS:
        assert ctx.states[stage.id]["status"] == ("completed" if stage.id < first_stale else "stale")
    assert all(isinstance(value, list) and len(value) == 1 and isinstance(value[0], str) and len(value[0]) == 64
        for record in ctx.states.values() for value in record.get("files", {}).values())


def test_b03_and_b02_recompute_archive_only_their_own_and_later_products(tmp_path):
    parent = make_run(tmp_path)
    analysis.analyze(parent, project_root=tmp_path)
    results = parent / "results"
    baseline = {name: ((results / name).read_bytes(), (results / name).stat().st_mtime_ns)
        for name in ("analysis_inputs.json", "drift_summary.csv", "drift_summary.json")}
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    ctx.execute(["B03"])
    backups = list(results.glob("before_*"))
    assert len(backups) == 1 and {p.name for p in backups[0].iterdir()} == set(analysis_plotting.REPORT_FILES)
    assert {name: ((results / name).read_bytes(), (results / name).stat().st_mtime_ns) for name in baseline} == baseline
    ctx.execute(["B02"])
    second = next(path for path in results.glob("before_*") if path not in backups)
    assert {p.name for p in second.iterdir()} == set(analysis_steps.TABLE_FILES + analysis_plotting.REPORT_FILES)
    assert (results / "analysis_inputs.json").read_bytes() == baseline["analysis_inputs.json"][0]
    assert ctx.states["B03"]["status"] == "stale"


def test_changed_bad_input_keeps_old_report_before_any_archive(tmp_path):
    parent = make_run(tmp_path)
    analysis.analyze(parent, project_root=tmp_path)
    old = (parent / "results/analysis.json").read_bytes()
    change_summary(parent, lambda p: p["linewidth"].update(hwhm_hz=float("inf")))
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    with pytest.raises(ValueError, match="有限数值"):
        ctx.execute(["B01", "B02", "B03"])
    assert (parent / "results/analysis.json").read_bytes() == old
    assert not list((parent / "results").glob("before_*"))
    assert json.loads((parent / "analysis_state.json").read_text(encoding="utf-8"))["steps"]["B01"]["status"] == "quality_failed"


def test_report_failure_and_secondary_diagnostic_failure_keep_original_and_b02(tmp_path, monkeypatch):
    parent = make_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    ctx.execute(["B01", "B02"])
    table = (parent / "results/drift_summary.json").read_bytes()
    failure = OSError("原始绘图保存失败")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(analysis_plotting, "save_figure", fail)
    persist = ctx.persist
    def persist_failure():
        if ctx.states["B03"]["status"] == "failed":
            raise OSError("诊断磁盘另失败")
        persist()
    monkeypatch.setattr(ctx, "persist", persist_failure)
    with pytest.raises(OSError) as caught:
        ctx.execute(["B03"])
    assert caught.value is failure
    assert ctx.states["B03"]["status"] == "failed"
    assert (parent / "results/drift_summary.json").read_bytes() == table
    if hasattr(failure, "__notes__"):
        assert "诊断磁盘另失败" in failure.__notes__[0]


def test_cancel_before_archive_keeps_outputs_and_blocks_following_stages(tmp_path, monkeypatch):
    parent = make_run(tmp_path)
    ctx = analysis.AnalysisWorkflow(parent, project_root=tmp_path)
    failure = WorkflowCancelled("模拟离线取消")
    def cancel():
        raise failure
    monkeypatch.setattr(analysis, "check_cancelled", cancel)
    with pytest.raises(WorkflowCancelled) as caught:
        ctx.execute(["B01", "B02", "B03"])
    assert caught.value is failure
    assert ctx.states["B01"]["status"] == "cancelled"
    assert not (parent / "results/drift_summary.csv").exists()


def test_module_subprocess_uses_explicit_temporary_run_and_generates_real_products(tmp_path):
    parent = make_run(tmp_path)
    yaml_file(tmp_path / "params/mapping.yaml", {})
    environment = os.environ.copy()
    environment.update(LAB_RUN_DIR=str(parent), LAB_TYPED_PARAMETERS="{}", MPLBACKEND="Agg", PYTHONUTF8="1")
    code = """from pathlib import Path
import runpy, sys
from lab_workflows.experiment_runtime import runtime_run_dir
root, expected = map(lambda value: Path(value).resolve(), sys.argv[1:])
assert expected.is_relative_to(root) and runtime_run_dir() == expected
runpy.run_module('lab_workflows.experiment_modules.mx_y_rf_sensitivity_drift.analysis', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(tmp_path), str(parent)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((parent / "analysis_state.json").read_text(encoding="utf-8"))
    assert all(record["status"] == "completed" for record in state["steps"].values())
    assert state["options"] == {}
    assert json.loads((parent / "results/analysis.json").read_text(encoding="utf-8"))["valid_cycle_count"] == 2
    assert (parent / "results/drift_trend.png").stat().st_size > 1000


def test_read_only_legacy_summary_sanitizes_json_without_claiming_stepwise_analysis(tmp_path):
    parent = make_run(tmp_path)
    assert analysis.read_summary(parent, project_root=tmp_path) == {}
    legacy = dict(success=True, run_dir=str(Path("old_project") / parent.name), cycle_count=3,
        rows=[dict(cycle_index=0, valid=False, sensitivity=float("nan"), linewidth=float("inf"))],
        files=["drift_summary.csv", "drift_trend.png"], plot_profile="paper")
    json_file(parent / "results/analysis.json", legacy)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()}
    value = analysis.read_summary(parent, project_root=tmp_path)
    assert value["success"] and value["rows"][0]["sensitivity"] is None and value["rows"][0]["linewidth"] is None
    json.dumps(value, allow_nan=False)
    snapshot = analysis.AnalysisWorkflow(parent, project_root=tmp_path).snapshot()
    assert snapshot["summary"]["status"] == "not_started" and all(s["status"] == "pending" for s in snapshot["steps"].values())
    assert not (parent / "analysis_state.json").exists()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in parent.rglob("*") if path.is_file()} == before
    change_yaml(parent / "experiment_config.yaml", lambda config: config.update(completion_status="running"))
    assert analysis.read_summary(parent, project_root=tmp_path)["success"]
    with pytest.raises(ValueError, match="仍在执行"):
        analysis.AnalysisWorkflow(parent, project_root=tmp_path)
