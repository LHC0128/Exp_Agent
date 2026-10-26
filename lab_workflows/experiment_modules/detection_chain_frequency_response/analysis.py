"""探测链路频率响应标定离线分析；不连接硬件，质量失败仍保存诊断。"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import yaml

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled, runtime_run_dir
from ...plotting import new_figure, save_figure, set_plot_style
from lab_workflows.workflow_state import read_state, write_state
from .models import DetectionChainFrequencyResponseParams
from .run_format import load_frequency_response, validate_run_identity
from .stages import ANALYSIS, invalidate, validate_selection
from lab_workflows.workflow_stages import initial_states

ANALYSIS_VERSION = "detection-chain-normalized-response-v2"


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


class AnalysisWorkflow:
    """持久化三个分析阶段；前置产物必须属于同一输入和参数修订。"""

    def __init__(self, run_dir, analysis_parameters=None):
        self.run_dir = Path(run_dir).resolve()
        self.results = self.run_dir / "results"
        self.state_path = self.run_dir / "analysis_state.json"
        self.config = yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8"))
        if not isinstance(self.config, dict):
            raise ValueError("运行配置必须为映射")
        validate_run_identity(self.config)
        if self.config.get("completion_status") == "running":
            raise ValueError("本轮扫描仍在写入数据，请等待扫描结束")
        self.params = DetectionChainFrequencyResponseParams.from_external(
            self.config.get("parameters", {}))
        # 本实验没有可调分析覆盖值；保留 options 结构以兼容会话接口。
        self.options: dict[str, float | str] = {}
        previous = read_state(self.state_path) or {}
        self.states = previous.get("steps") or initial_states(ANALYSIS)
        self.revision = previous.get("revision", 0)
        self.summary = previous.get("summary") or {}
        raw = [{"name": path.name, "bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
            for path in sorted((self.run_dir / "raw").glob("*")) if path.is_file()]
        # 适配器会写分析状态；它不是采集输入，不能让刚完成的阶段立即失效。
        acquisition_config = {key: value for key, value in self.config.items()
                              if key not in ("analysis_status", "analysis_error")}
        self.fingerprint = dict(config_sha256=hashlib.sha256(json.dumps(acquisition_config,
            sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest(),
            raw_files=raw, analysis_version=ANALYSIS_VERSION)
        if previous and previous.get("fingerprint") != self.fingerprint:
            invalidate(ANALYSIS, self.states, "B01")
            self.revision += 1
        for stage in ANALYSIS:
            record = self.states[stage.id]
            if record["status"] == "running":
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
            if record["status"] == "completed" and (
                    set(record.get("files", {})) != set(stage.artifacts)
                    or any(signature is None or self._file_signature(self.results / name) != signature
                           for name, signature in record.get("files", {}).items())):
                invalidate(ANALYSIS, self.states, stage.id)
                self.revision += 1
                break
        if analysis_parameters:
            raise ValueError("不是分析参数：" + "、".join(sorted(analysis_parameters)))

    @staticmethod
    def _file_signature(path):
        return [path.stat().st_size, path.stat().st_mtime_ns] if path.is_file() else None

    def snapshot(self):
        return dict(run_id=self.run_dir.name, revision=self.revision, options=self.options,
            steps=self.states, fingerprint=self.fingerprint, summary=self.summary)

    def persist(self):
        from ...experiment_runtime import report_runtime_progress
        write_state(self.state_path, self.snapshot())
        if self.states["B01"]["status"] != "running":
            write_json(self.results / "analysis_summary.json", self.summary)
        report_runtime_progress("analysis", f"分析状态：{self.summary.get('status', 'not_started')}",
            data={"analysis_workflow": self.snapshot()})

    def _archive_from(self, item):
        index = next(i for i, stage in enumerate(ANALYSIS) if stage.id == item)
        names = {name for stage in ANALYSIS[index:] for name in stage.artifacts}
        files = [path for path in self.results.glob("*") if path.is_file() and path.name in names]
        if files:
            summary_path = self.results / "analysis_summary.json"
            if summary_path.is_file():
                files.append(summary_path)
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in files:
                shutil.move(str(path), str(backup / path.name))
            self.summary["backup_directory"] = backup.name

    def execute(self, selected, expected_revision=None):
        # 长存活上下文也不能在目录身份改变后写入状态或归档产物。
        validate_run_identity(yaml.safe_load((self.run_dir / "experiment_config.yaml").read_text(encoding="utf-8")))
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("分析状态已改变，请刷新后重试")
        selected = validate_selection(ANALYSIS, self.states, selected)
        self.results.mkdir(exist_ok=True)
        self.revision += 1
        set_plot_style("paper")
        for item in selected:
            check_cancelled()
            invalidate(ANALYSIS, self.states, item)
            summary_keys = {"B02": ("response",), "B03": ("plot",)}
            stage_index = next(i for i, stage in enumerate(ANALYSIS) if stage.id == item)
            for stage in ANALYSIS[stage_index:]:
                for key in summary_keys.get(stage.id, ()):
                    self.summary.pop(key, None)
            if item != "B01":
                self._archive_from(item)
            self.states[item] = {"status": "running", "error": ""}
            self.summary.update(status="running")
            self.summary.pop("error", None)
            self.persist()
            try:
                getattr(self, "step_" + item)()
                files = {name: self._file_signature(self.results / name)
                         for stage in ANALYSIS if stage.id == item for name in stage.artifacts}
                if any(signature is None for signature in files.values()):
                    raise RuntimeError("分析产物未完整保存")
                self.states[item] = {"status": "completed", "error": "",
                    "files": files}
                if item != "B03":
                    self.summary["status"] = "partial"
            except BaseException as exc:
                cancelled = isinstance(exc, (WorkflowCancelled, KeyboardInterrupt))
                status = "cancelled" if cancelled else "quality_failed" if isinstance(exc, ValueError) else "failed"
                self.states[item] = {"status": status, "error": str(exc)}
                self.summary.update(status=status, error=str(exc))
                raise
            finally:
                try:
                    self.persist()
                except Exception:
                    if self.states[item]["status"] == "completed":
                        raise
        return self.summary

    def step_B01(self):
        """核对身份与原始格式，通过后归档旧产物并初始化分析摘要。"""
        load_frequency_response(self.run_dir, self.config)
        self.results.mkdir(exist_ok=True)
        existing = [path for path in self.results.iterdir() if path.is_file()]
        backup = None
        if existing:
            backup = self.results / ("before_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup.mkdir()
            for path in existing:
                shutil.move(str(path), str(backup / path.name))
        self.summary = dict(analysis_version=ANALYSIS_VERSION, status="running", options=self.options,
                       plotting=dict(profile="paper", figure_kind="wide",
                                      standard_height_mm=120, panels=4, paired_exports=True, dpi=300),
                       backup_directory=backup.name if backup else None, warnings=[])
        set_plot_style("paper")

    def step_B02(self):
        """排除零基带点，按两侧最近有效点归一化并计算边带下降斜率。"""
        am_freqs, peak_amps = load_frequency_response(self.run_dir, self.config)
        # 历史数据也排除零基带点：Welch 默认去均值，原有谱峰不是直流响应。
        zero_baseband = np.isclose(am_freqs, self.params.hf2_osc_freq_hz, rtol=0, atol=1e-9)
        peak_amps = np.where(zero_baseband, np.nan, peak_amps)
        valid = np.isfinite(peak_amps) & (peak_amps > 0)
        if not np.any(valid):
            raise ValueError("无有效谱峰点，无法归一化；请检查采集数据或谱峰提取设置")
        # 两侧各取最近的有效点，避免用已经失效的零基带点归一化。
        reference_indices = []
        for side in (am_freqs < self.params.hf2_osc_freq_hz, am_freqs > self.params.hf2_osc_freq_hz):
            candidates = np.flatnonzero(valid & side)
            if candidates.size:
                reference_indices.append(candidates[np.argmin(abs(am_freqs[candidates] - self.params.hf2_osc_freq_hz))])
        norm_factor = float(np.mean(peak_amps[reference_indices]))
        peak_amps_norm = peak_amps / norm_factor
        baseband_freqs = np.abs(am_freqs - self.params.hf2_osc_freq_hz)

        print(f"\n有效数据点: {np.sum(valid)}/{len(am_freqs)}")
        print(f"归一化参考: {norm_factor:.3e} V/√Hz，AM 频率 {am_freqs[reference_indices].tolist()} Hz")
        slopes = self._slope_summary(am_freqs, peak_amps_norm, valid)
        np.savez(
            self.results / "analysis_results.npz",
            am_frequencies_hz=am_freqs,
            peak_amplitudes=peak_amps,
            peak_amplitudes_normalized=peak_amps_norm,
            baseband_frequencies_hz=baseband_freqs,
            norm_factor=norm_factor,
            normalization_reference_am_frequencies_hz=am_freqs[reference_indices],
            excluded_zero_baseband=zero_baseband,
        )
        self.summary["response"] = dict(
            valid_points=int(np.sum(valid)), total_points=int(am_freqs.size),
            norm_factor_v_per_sqrt_hz=norm_factor,
            normalization_reference_am_frequencies_hz=[float(item) for item in am_freqs[reference_indices]],
            zero_baseband_points=int(zero_baseband.sum()),
            slopes_percent_per_khz=slopes,
        )
        self.summary["warnings"].append(
            "零基带点（AM 频率等于解调频率）是去均值 Welch 的直流，不参与响应与归一化。")
        self.summary["warnings"].append(
            "上下边带斜率仅与噪声谱实验的 -0.010%/kHz 对照，不按比例推算噪声贡献。")

    def _slope_summary(self, am_freqs, peak_amps_norm, valid):
        """计算 15–25 kHz 基带（±15/±25 kHz 边带）的响应下降斜率。"""

        def response_at(freq):
            index = np.argmin(np.abs(am_freqs - freq))
            return float(peak_amps_norm[index]) if valid[index] else float("nan")

        resp = {freq: response_at(freq) for freq in (75000.0, 105000.0, 65000.0, 115000.0)}
        slopes = {}

        def slope(label, near, far, span_khz=10.0):
            if np.isfinite(resp[near]) and np.isfinite(resp[far]):
                drop = (resp[far] - resp[near]) / resp[near] * 100
                slopes[label] = drop / span_khz
                print(f"{label}: {drop:+.4f}% 总下降，斜率 {slopes[label]:+.4f}% / kHz (基带)")

        slope("上边带 (105→115 kHz)", 105000.0, 115000.0)
        slope("下边带 (75→65 kHz)", 75000.0, 65000.0)
        if slopes.get("上边带 (105→115 kHz)") is not None and slopes.get("下边带 (75→65 kHz)") is not None:
            avg_15k = (resp[75000.0] + resp[105000.0]) / 2
            avg_25k = (resp[65000.0] + resp[115000.0]) / 2
            drop_dsb = (avg_25k - avg_15k) / avg_15k * 100
            slopes["双边带平均 (15–25 kHz 基带)"] = drop_dsb / 10
            print(f"\n双边带平均 (15-25 kHz 基带): {drop_dsb:+.4f}% 总下降")
            print(f"  斜率: {slopes['双边带平均 (15–25 kHz 基带)']:+.4f}% / kHz")
            print("\n与噪声谱实验对比:")
            print("  噪声谱 N_S1 下降: -0.010% / kHz")
            print("  两种斜率仅作对照，不按比例推算噪声贡献。")
        return slopes

    def step_B03(self):
        """绘制四联响应曲线并完成摘要。"""
        with np.load(self.results / "analysis_results.npz") as saved:
            am_freqs = saved["am_frequencies_hz"]
            peak_amps = saved["peak_amplitudes"]
            peak_amps_norm = saved["peak_amplitudes_normalized"]
            baseband_freqs = saved["baseband_frequencies_hz"]

        fig, axes = new_figure(nrows=2, ncols=2, kind="wide", height_mm=120)

        # (a) 归一化响应 vs AM 频率
        ax = axes[0, 0]
        ax.plot(am_freqs / 1000, peak_amps_norm, 'bo-', markersize=4, linewidth=1.5)
        ax.axvline(self.params.hf2_osc_freq_hz / 1000, color='gray', linestyle='--', alpha=0.5, label='Zero baseband (excluded)')
        ax.axvline(75, color='r', linestyle='--', alpha=0.5)
        ax.axvline(105, color='r', linestyle='--', alpha=0.5, label='±15 kHz sidebands')
        ax.axvline(65, color='orange', linestyle='--', alpha=0.5)
        ax.axvline(115, color='orange', linestyle='--', alpha=0.5, label='±25 kHz sidebands')
        ax.set_xlabel('AM Modulation Frequency (kHz)')
        ax.set_ylabel('Normalized Response')
        ax.set_title('(a) Detection Chain Frequency Response')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        # (b) 绝对响应幅度
        ax = axes[0, 1]
        ax.plot(am_freqs / 1000, peak_amps, 'go-', markersize=4, linewidth=1.5)
        ax.axvline(self.params.hf2_osc_freq_hz / 1000, color='gray', linestyle='--', alpha=0.5)
        ax.set_xlabel('AM Modulation Frequency (kHz)')
        ax.set_ylabel('Peak ASD (V/sqrt(Hz))')
        ax.set_title('(b) Absolute Response ASD')
        ax.grid(True, alpha=0.3)
        ax.set_yscale('log')

        # (c) 基带频率表示
        ax = axes[1, 0]
        ax.plot(baseband_freqs / 1000, peak_amps_norm, 'mo-', markersize=4, linewidth=1.5)
        ax.axvline(15, color='r', linestyle='--', alpha=0.5, label='15 kHz')
        ax.axvline(25, color='orange', linestyle='--', alpha=0.5, label='25 kHz')
        ax.set_xlabel('Baseband Frequency (kHz)')
        ax.set_ylabel('Normalized Response')
        ax.set_title('(c) Response vs Baseband Frequency')
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        # (d) 相对变化百分比
        ax = axes[1, 1]
        rel_change = (peak_amps_norm - 1) * 100
        ax.plot(baseband_freqs / 1000, rel_change, 'ko-', markersize=4, linewidth=1.5)
        ax.axhline(0, color='gray', linestyle=':', alpha=0.5)
        ax.axvline(15, color='r', linestyle='--', alpha=0.5)
        ax.axvline(25, color='orange', linestyle='--', alpha=0.5)
        ax.set_xlabel('Baseband Frequency (kHz)')
        ax.set_ylabel('Relative Change (%)')
        ax.set_title('(d) Response Change Relative to Nearest Valid Points')
        ax.grid(True, alpha=0.3)

        fig.set_layout_engine("constrained")
        save_figure(fig, self.results / "frequency_response.png", close=True)
        self.summary["plot"] = dict(figure="frequency_response.png", panels=[
            "normalized response", "absolute ASD", "baseband response", "relative change"])
        self.summary["status"] = "completed"
        self.summary["warnings"] = list(dict.fromkeys(self.summary["warnings"]))
        print(f"\n结果已保存至: {self.results}")


def analyze(run_dir: Path, *, step_ids=None, analysis_parameters=None, expected_revision=None):
    """完整分析和分阶段分析复用相同三个步骤。"""
    ctx = AnalysisWorkflow(run_dir, analysis_parameters)
    return ctx.execute(step_ids or [stage.id for stage in ANALYSIS], expected_revision)


def main() -> int:
    import os
    command = json.loads(os.environ.get("LAB_TYPED_PARAMETERS", "{}"))
    summary = analyze(runtime_run_dir(), step_ids=command.get("step_ids"),
        analysis_parameters=command.get("analysis_parameters"), expected_revision=command.get("revision"))
    return 0 if summary["status"] in ("completed", "partial") else 2


if __name__ == "__main__":
    raise SystemExit(main())
