"""网格输入资格、内容指纹及既有双斜率科学分析的接线。"""
from __future__ import annotations
from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import yaml

from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled
from ..mx_y_rf_grid_analysis import GridAnalysisSpec, GridAxisSpec, analyze_mx_y_rf_grid
from ..mx_y_rf_sensitivity.analysis_plotting import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from .models import MxYRFProbeDetuningOptimizationParams
from .stages import ANALYSIS_KEYS
from . import run_format

GRID_SPEC = GridAnalysisSpec(
    experiment_id="mx-y-rf-probe-detuning-optimization",
    outer=GridAxisSpec(
        manifest_key="pzt_voltage_v",
        index_key="pzt_index",
        coordinate_key="pzt_voltage_v",
        axis_label="PZT scan offset (V)",
        point_label="PZT",
    ),
    inner=GridAxisSpec(
        manifest_key="probe_power_v",
        index_key="probe_index",
        coordinate_key="probe_power_v",
        axis_label="Probe power control voltage (V)",
        point_label="Probe",
    ),
    point_record_fields=(
        "pzt_voltage_readback_v",
        "pzt_actual_v",
        "pzt_setpoint_error_v",
    ),
)


def analyze(run_dir: Path) -> dict:
    return analyze_mx_y_rf_grid(
        run_dir,
        params_type=MxYRFProbeDetuningOptimizationParams,
        spec=GRID_SPEC,
        evaluate_point=evaluate_mx_y_rf_point,
        plot_full_analysis=_plot_full_analysis,
    )


def main() -> int:
    analyze(runtime_run_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


class AnalysisQualityError(ValueError):
    """原始输入不合格或无可用最优点，保留诊断。"""


def contained(path, root):
    path = Path(path)
    resolved = path.resolve()
    if not resolved.is_relative_to(Path(root).resolve()) or resolved != path.absolute():
        raise AnalysisQualityError(f"文件引用超出运行目录或经过符号链接/目录联接: {path}")
    return path


def signature(path):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]


def load_config(run_dir):
    path = contained(run_dir / "experiment_config.yaml", run_dir)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise AnalysisQualityError(reason)
    params = MxYRFProbeDetuningOptimizationParams.from_external(
        config["parameters"], schema_version=int(config.get("schema_version", 1)))
    return config, params


def resolve_options(params, options):
    if unknown := options.keys() - set(ANALYSIS_KEYS):
        raise ValueError("不支持的分析覆盖参数：" + "、".join(sorted(unknown)))
    resolved = MxYRFProbeDetuningOptimizationParams.from_external(params.to_external() | options)
    for field in fields(resolved):
        key = field.metadata.get("external_name")
        if key not in ANALYSIS_KEYS:
            continue
        value = getattr(resolved, field.name)
        if not math.isfinite(value):
            raise ValueError(f"{key} 必须为有限数值")
        for bound, comparison in (("minimum", lambda x, y: x < y), ("maximum", lambda x, y: x > y)):
            limit = field.metadata.get(bound)
            if limit is not None and comparison(value, limit):
                raise ValueError(f"{key} 超出模型的 {bound} 限制")
    if resolved.y_rf_nt_per_vpp <= 0:
        raise ValueError("Y_RF_NT_PER_VPP 必须大于 0")
    return resolved


def fingerprint(run_dir):
    config, _ = load_config(run_dir)
    config = {key: value for key, value in config.items() if key not in ("analysis_status", "analysis_error")}
    raw = contained(run_dir / "raw", run_dir)
    files = {path.relative_to(run_dir).as_posix(): signature(contained(path, run_dir))
        for path in sorted(raw.rglob("*")) if path.is_file()}
    return dict(config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        raw_files=files, analysis_version="probe-pzt-grid-v1")


def load_inputs(run_dir):
    config, params = load_config(run_dir)
    raw = contained(run_dir / "raw", run_dir)
    manifest = yaml.safe_load(contained(raw / "point_manifest.yaml", run_dir).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("points"), dict):
        raise AnalysisQualityError("无有效点状态清单")
    axes = [np.asarray(manifest[key], dtype=float) for key in ("pzt_voltage_v", "probe_power_v")]
    if any(axis.ndim != 1 or not len(axis) or not np.all(np.isfinite(axis))
            or (len(axis) > 1 and np.any(np.diff(axis) <= 0)) for axis in axes):
        raise AnalysisQualityError("网格轴必须是非空有限升序数组")
    if params.noise_n_avg < 1:
        raise AnalysisQualityError("噪声记录数必须为正整数")
    indices = set()
    for key, record in manifest["points"].items():
        check_cancelled()
        if not isinstance(key, str) or re.fullmatch(r"pzt_\d+_probe_\d+", key) is None or not isinstance(record, dict):
            raise AnalysisQualityError("点目录或点记录格式无效")
        index = tuple(record.get(name) for name in ("pzt_index", "probe_index"))
        if any(type(i) is not int or not 0 <= i < len(axis) for i, axis in zip(index, axes)) or index in indices:
            raise AnalysisQualityError("网格索引越界或重复")
        indices.add(index)
        if any(not np.isclose(record.get(name, np.nan), axis[i], rtol=0, atol=1e-12)
                for name, i, axis in zip(("pzt_voltage_v", "probe_power_v"), index, axes)):
            raise AnalysisQualityError("点坐标与网格轴不一致")
        point = contained(raw / "points" / key, run_dir)
        for path in point.glob("*"):
            contained(path, run_dir)
        # 不完整点沿用旧算法的诊断规则，不伪造成有效点。
        if record.get("status") != "completed":
            continue
        amplitude = contained(point / "amplitude_scan.npz", run_dir)
        with np.load(amplitude, allow_pickle=False) as data:
            x = data["signed_amplitude_vpp"]
            y = data["r_mean_v"] if "r_mean_v" in data else data["r_scalar_mean_v"]
            std = data["r_std_v"]
            if x.ndim != 1 or len(x) < 5 or y.shape != x.shape or std.shape != x.shape:
                raise AnalysisQualityError(f"{key} 幅度响应形状不符")
        for i in range(params.noise_n_avg):
            with np.load(contained(point / f"noise_{i:03d}.npz", run_dir), allow_pickle=False) as data:
                rate = float(data["actual_rate_sa_s"])
                if data["r_v"].ndim != 1 or len(data["r_v"]) < 8 or not np.all(np.isfinite(data["r_v"])) or not math.isfinite(rate) or rate <= 0:
                    raise AnalysisQualityError(f"{key} 噪声数据或实际速率无效")
    if not indices or len(indices) != len(axes[0]) * len(axes[1]):
        raise AnalysisQualityError("网格清单未完整声明全部点")
    for path in (run_dir / "results").rglob("*"):
        contained(path, run_dir)
    return dict(experiment_id=run_format.EXPERIMENT_ID,
        total_point_count=len(indices), shape=[len(axis) for axis in axes])


class _GridCancelled(BaseException):
    """穿过旧共享分析器的单点 Exception 捕获，避免取消被当作坏点吞掉。"""


def evaluate(run_dir, params):
    def point(path, _params, **kwargs):
        try:
            check_cancelled()
            return evaluate_mx_y_rf_point(path, params, **kwargs)
        except WorkflowCancelled as exc:
            raise _GridCancelled(exc) from exc

    def plot(**kwargs):
        check_cancelled()
        return _plot_full_analysis(**(kwargs | {"params": params}))

    check_cancelled()
    try:
        return analyze_mx_y_rf_grid(run_dir, params_type=MxYRFProbeDetuningOptimizationParams,
            spec=GRID_SPEC, evaluate_point=point, plot_full_analysis=plot)
    except _GridCancelled as exc:
        raise exc.args[0]
    except RuntimeError as exc:
        if str(exc).startswith("未找到可用最优点"):
            raise AnalysisQualityError(str(exc)) from exc
        raise
