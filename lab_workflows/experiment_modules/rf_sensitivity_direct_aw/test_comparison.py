"""只读历史结果、真实对比子进程和简洁图形的回归测试。"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from lab_workflows.common import CancellationToken, WorkflowCancelled
from . import comparison
from .definition import comparison_runner
from .run_format import COMPARISON_FILES, EXPERIMENT_ID, comparison_block_reason


def result_run(directory: Path, *, fitted=True, points=11, frequency=None) -> dict:
    directory.mkdir(parents=True)
    config = {"experiment_id": EXPERIMENT_ID, "schema_version": 2, "execution_mode": "typed_workflow",
              "acquisition_method": "direct_aw_x", "parameters": {}, "completion_status": "completed",
              "phase_calibration_status": "completed", "xy_phase_calibration": {"converged": True},
              "analysis_status": "completed"}
    (directory / "experiment_config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    output = directory / "results"
    output.mkdir()
    analysis = {"response_fit": {"success": fitted}, "frequency_response_correction": {"applied": fitted}}
    (output / "analysis.yaml").write_text(yaml.safe_dump(analysis), encoding="utf-8")
    field = np.linspace(-points, points, points)
    response = field / (field**2 + 4)
    fit_field = np.linspace(-points, points, 31) if fitted else np.array([])
    np.savez(output / "response_fit.npz", success=fitted, amplitudes_nT=field, demod3_x_V=response,
             fit_x_nT=fit_field, fit_y_V=fit_field / (fit_field**2 + 4))
    frequency = np.asarray([0, 1, 4, 10, 100] if frequency is None else frequency, dtype=float)
    np.savez(output / "sensitivity.npz", freq_Hz=frequency,
             sensitivity_raw_fT_per_sqrtHz=np.full(len(frequency), 7.0),
             sensitivity_corrected_fT_per_sqrtHz=np.full(len(frequency), 14.0))
    return {"run_id": directory.name, "legend": "", "run_dir": str(directory)}


def snapshot(directory: Path) -> dict:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def test_overlay_uses_saved_arrays_legends_and_real_exports(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    first = result_run(sources / "first")
    second = result_run(sources / "second", points=17, frequency=[0, 2, 8, 20, 200])
    first["legend"] = "Control A"
    second["legend"] = "Control B"
    before = snapshot(sources)
    save = comparison.save_figure

    def inspect(figure, path):
        upper, lower = figure.axes
        assert len(upper.lines) == 4
        assert len(lower.lines) == 2
        assert not upper.texts and not lower.texts
        assert [text.get_text() for text in figure.legends[0].get_texts()] == ["Control A", "Control B"]
        assert lower.get_xscale() == lower.get_yscale() == "log"
        for index, item in enumerate((first, second)):
            with np.load(Path(item["run_dir"]) / "results/response_fit.npz") as data:
                np.testing.assert_array_equal(upper.lines[2 * index].get_xdata(), data["amplitudes_nT"])
                np.testing.assert_array_equal(upper.lines[2 * index + 1].get_ydata(), data["fit_y_V"])
            with np.load(Path(item["run_dir"]) / "results/sensitivity.npz") as data:
                mask = data["freq_Hz"] > 0
                np.testing.assert_array_equal(lower.lines[index].get_xdata(), data["freq_Hz"][mask])
                np.testing.assert_array_equal(lower.lines[index].get_ydata(), data["sensitivity_corrected_fT_per_sqrtHz"][mask])
            assert upper.lines[2 * index].get_color() == upper.lines[2 * index + 1].get_color() == lower.lines[index].get_color()
        return save(figure, path)

    monkeypatch.setattr(comparison, "save_figure", inspect)
    output = tmp_path / "comparison"
    result = comparison.compare_runs([first, second], output)
    assert result["warnings"] == []
    assert snapshot(sources) == before
    assert (output / COMPARISON_FILES[0]).read_bytes().startswith(b"\x89PNG")
    assert (output / COMPARISON_FILES[1]).read_bytes().startswith(b"%PDF")
    assert json.loads((output / "comparison.json").read_text(encoding="utf-8")) == result


def test_chinese_run_names_use_english_default_legends(tmp_path, monkeypatch):
    runs = [result_run(tmp_path / "1013_093027_最佳"), result_run(tmp_path / "常数")]
    save = comparison.save_figure
    def inspect(figure, path):
        assert [text.get_text() for text in figure.legends[0].get_texts()] == ["Run 1013_093027", "Run 2"]
        return save(figure, path)
    monkeypatch.setattr(comparison, "save_figure", inspect)
    result = comparison.compare_runs(runs, tmp_path / "comparison")
    assert [item["legend"] for item in result["runs"]] == ["Run 1013_093027", "Run 2"]


def test_numeric_fallback_uses_raw_spectrum_and_reports_warning(tmp_path, monkeypatch):
    runs = [result_run(tmp_path / "good"), result_run(tmp_path / "fallback", fitted=False)]
    save = comparison.save_figure

    def inspect(figure, path):
        assert len(figure.axes[0].lines) == 3
        np.testing.assert_array_equal(figure.axes[1].lines[1].get_ydata(), [7, 7, 7, 7])
        assert [text.get_text() for text in figure.legends[0].get_texts()] == ["good", "fallback"]
        return save(figure, path)

    monkeypatch.setattr(comparison, "save_figure", inspect)
    result = comparison.compare_runs(runs, tmp_path / "comparison")
    assert "fallback" in result["warnings"][0]
    assert "未作洛伦兹校正" in result["warnings"][0]


@pytest.mark.parametrize("problem", ["duplicate", "single", "missing", "old", "shape", "nonfinite", "frequency", "negative", "object", "legend", "path"])
def test_invalid_inputs_fail_before_export(tmp_path, problem):
    runs = [result_run(tmp_path / "first"), result_run(tmp_path / "second")]
    directory = Path(runs[1]["run_dir"])
    if problem == "duplicate":
        runs[1] = runs[0]
    elif problem == "single":
        runs = runs[:1]
    elif problem == "missing":
        (directory / "results/sensitivity.npz").unlink()
    elif problem == "old":
        path = directory / "experiment_config.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["schema_version"] = 1
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
    elif problem in {"shape", "nonfinite", "frequency", "negative", "object"}:
        frequency = [0, 1, 2] if problem != "frequency" else [0, 2, 1]
        values = {"shape": [1, 2], "nonfinite": [1, np.nan, 2], "frequency": [1, 2, 3],
                  "negative": [1, -1, 2], "object": np.array([1, 2, 3], dtype=object)}[problem]
        np.savez(directory / "results/sensitivity.npz", freq_Hz=frequency, sensitivity_corrected_fT_per_sqrtHz=values)
    elif problem == "legend":
        runs[1]["legend"] = "中文图例"
    elif problem == "path":
        runs[1]["run_id"] = "..\\outside"
    output = tmp_path / "comparison"
    with pytest.raises(ValueError):
        comparison.compare_runs(runs, output)
    assert not output.exists()


def test_cancel_after_export_removes_partial_outputs(tmp_path, monkeypatch):
    runs = [result_run(tmp_path / "first"), result_run(tmp_path / "second")]
    output = tmp_path / "comparison"

    def cancel():
        if (output / COMPARISON_FILES[0]).exists():
            raise WorkflowCancelled("已取消")

    monkeypatch.setattr(comparison, "check_cancelled", cancel)
    with pytest.raises(WorkflowCancelled):
        comparison.compare_runs(runs, output)
    assert not output.exists()


def test_export_failure_does_not_publish_partial_files(tmp_path, monkeypatch):
    runs = [result_run(tmp_path / "first"), result_run(tmp_path / "second")]
    output = tmp_path / "comparison"

    def fail_pdf(figure, path):
        path.write_bytes(b"partial png")
        raise OSError("PDF 导出失败")

    monkeypatch.setattr(comparison, "save_figure", fail_pdf)
    with pytest.raises(OSError, match="PDF 导出失败"):
        comparison.compare_runs(runs, output)
    assert not output.exists()


def test_existing_output_directory_is_preserved(tmp_path):
    output = tmp_path / "comparison"
    output.mkdir()
    manifest = output / "comparison.json"
    manifest.write_text("已有结果", encoding="utf-8")
    with pytest.raises(ValueError, match="输出目录已存在"):
        comparison_runner([], output)
    assert manifest.read_text(encoding="utf-8") == "已有结果"


def test_real_comparison_subprocess_and_cancel_token(tmp_path):
    runs = [result_run(tmp_path / "first"), result_run(tmp_path / "second")]
    before = snapshot(tmp_path)
    output = tmp_path / "comparison"
    result = comparison_runner(runs, output)
    assert result["runs"][0]["legend"] == "first"
    assert all((output / name).stat().st_size > 100 for name in COMPARISON_FILES)
    assert {key: value for key, value in snapshot(tmp_path).items() if not key.startswith("comparison/")} == before
    cancellation = CancellationToken()
    cancellation.cancel()
    with pytest.raises(WorkflowCancelled):
        comparison_runner(runs, tmp_path / "cancelled", cancellation=cancellation)
    assert not (tmp_path / "cancelled/comparison.json").exists()


def test_real_subprocess_cancel_during_export_withholds_manifest(tmp_path):
    runs = [result_run(tmp_path / "first"), result_run(tmp_path / "second")]
    before = snapshot(tmp_path)
    output = tmp_path / "comparison"
    cancellation = CancellationToken()

    def progress(event):
        if event.percent == 80:
            cancellation.cancel()

    with pytest.raises(WorkflowCancelled):
        comparison_runner(runs, output, progress=progress, cancellation=cancellation)
    assert cancellation.cancelled
    assert not (output / "comparison.json").exists()
    assert {key: value for key, value in snapshot(tmp_path).items() if not key.startswith("comparison/")} == before


def test_comparison_eligibility_and_source_output_boundary(tmp_path):
    first = result_run(tmp_path / "first")
    second = result_run(tmp_path / "second")
    config = yaml.safe_load((tmp_path / "first/experiment_config.yaml").read_text(encoding="utf-8"))
    assert comparison_block_reason(tmp_path / "first", config) == ""
    config["analysis_status"] = "running"
    assert comparison_block_reason(tmp_path / "first", config)
    with pytest.raises(ValueError, match="源运行目录"):
        comparison.compare_runs([first, second], tmp_path / "first/results/comparison")
