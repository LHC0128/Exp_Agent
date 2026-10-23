"""原网格科学算法、输入资格和报告；不连接仪器、不改写采集参数。"""
import json
from pathlib import Path
import numpy as np
import yaml
from ...common import WorkflowCancelled
from ...experiment_runtime import check_cancelled
from ..mx_y_rf_grid_analysis import GridAnalysisSpec, GridAxisSpec, analyze_mx_y_rf_grid
from ..mx_y_rf_sensitivity.analysis_plotting import _plot_full_analysis
from ..mx_y_rf_sensitivity.point_analysis import evaluate_mx_y_rf_point
from ..mx_y_rf_sensitivity.analysis_steps import plain, AnalysisQualityError
from .models import MxYRFPowerOptimizationParams
from . import run_format

GRID_SPEC = GridAnalysisSpec(
    experiment_id=run_format.EXPERIMENT_ID,
    outer=GridAxisSpec(manifest_key="pump_power_v", index_key="pump_index", coordinate_key="pump_power_v",
        axis_label="Pump power control voltage (V)", point_label="Pump"),
    inner=GridAxisSpec(manifest_key="probe_power_v", index_key="probe_index", coordinate_key="probe_power_v",
        axis_label="Probe power control voltage (V)", point_label="Probe"))


def load_config(run_dir):
    config = yaml.safe_load((Path(run_dir) / "experiment_config.yaml").read_text(encoding="utf-8"))
    if reason := run_format.analysis_block_reason(config):
        raise ValueError(reason)
    return config


def load_inputs(run_dir):
    run_dir = Path(run_dir).resolve()
    config = load_config(run_dir)
    params = MxYRFPowerOptimizationParams.from_external(config["parameters"], schema_version=config.get("schema_version", 1))
    manifest = yaml.safe_load((run_dir / "raw/point_manifest.yaml").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("scan_order") != "pump_outer_probe_inner_serpentine"
            or not isinstance(manifest.get("points"), dict) or not manifest["points"]):
        raise ValueError("缺少有效光功率网格清单")
    axes = [np.asarray(manifest[key], dtype=float) for key in ("pump_power_v", "probe_power_v")]
    if any(axis.ndim != 1 or not axis.size or not np.all(np.isfinite(axis))
            or np.any(np.diff(axis) <= 0) for axis in axes):
        raise ValueError("光功率轴必须是有限、严格升序的一维数组")
    seen = set()
    for key, record in manifest["points"].items():
        check_cancelled()
        i, j = record["pump_index"], record["probe_index"]
        if (type(i) is not int or type(j) is not int or not 0 <= i < len(axes[0]) or not 0 <= j < len(axes[1])
                or key != f"pump_{i:03d}_probe_{j:03d}" or (i, j) in seen
                or not np.isclose(record["pump_power_v"], axes[0][i])
                or not np.isclose(record["probe_power_v"], axes[1][j])):
            raise ValueError("点索引、路径或功率与扫描轴不一致")
        seen.add((i, j))
        point = run_dir / "raw/points" / key
        if run_dir not in point.resolve().parents:
            raise ValueError("逐点数据路径越界")
        # 波形缺失、损坏或质量不合格由原逐点分析器记录原因，
        # 不能因一个坏点阻止其它功率点的矩阵与诊断导出。
    return params, config


class _AnalysisCancelled(BaseException):
    """穿过原网格分析器的逐点 Exception 诊断捕获，不把取消当作坏点。"""


def evaluate(run_dir, params):
    """只替换允许覆盖的分析参数；轴顺序、科学计算、双排名与图仍复用原实现。"""
    def checkpoint():
        try:
            check_cancelled()
        except WorkflowCancelled as exc:
            raise _AnalysisCancelled() from exc

    def point(raw, captured_params, **kwargs):
        checkpoint()
        return evaluate_mx_y_rf_point(raw, params, **kwargs)

    def plot(**kwargs):
        checkpoint()
        return _plot_full_analysis(**(kwargs | {"params": params}))

    try:
        return analyze_mx_y_rf_grid(Path(run_dir), params_type=MxYRFPowerOptimizationParams,
            spec=GRID_SPEC, evaluate_point=point, plot_full_analysis=plot)
    except _AnalysisCancelled as exc:
        raise exc.__cause__
    except RuntimeError as exc:
        path = Path(run_dir) / "results/optimization.yaml"
        if path.is_file() and yaml.safe_load(path.read_text(encoding="utf-8")).get("success") is False:
            raise AnalysisQualityError(str(exc)) from exc
        raise


def write_report(run_dir, options):
    results = Path(run_dir) / "results"
    payload = yaml.safe_load((results / "optimization.yaml").read_text(encoding="utf-8"))
    payload = plain(payload | {"analysis_parameters": options})
    (results / "analysis.yaml").write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (results / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return payload
