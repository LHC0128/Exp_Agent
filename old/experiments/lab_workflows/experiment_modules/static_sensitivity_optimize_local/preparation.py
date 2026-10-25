"""全部候选预检和计划；导入、schema 和预检不连接仪器。"""
from ..static_sensitivity.preparation import preflight as point_preflight
from .models import StaticSensitivityOptimizeLocalParams


def preflight(params, root):
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    _, mapping = point_preflight(params.point_parameters(run_tag=params.run_tag), root)
    # 所有候选已由模型调用单点模型完整校验；扫描轴不改变锁相频率或映射。
    return params, mapping


def planned_records(params: StaticSensitivityOptimizeLocalParams):
    rows = []
    for sequence in params.sequence():
        for value in sequence["values"]:
            name = sequence["parameter"]
            rows.append(dict(iteration=len(rows) + 1, parameter=name, value=value,
                is_baseline=value == params.baseline_params[name], status="pending", run_id=None,
                fit_is_valid=False, success=False, error="", fit_rejection_reasons="",
                sens_flat_fT_per_sqrt_Hz=None, fit_r2=None, gamma_nT=None, slope_V_per_fT=None))
    return rows


def point_parameters(ctx, record):
    return ctx.params.point_parameters(record["parameter"], record["value"],
        run_tag=f"{ctx.directory.root.name}_{record['iteration']:02d}_{record['parameter']}")
