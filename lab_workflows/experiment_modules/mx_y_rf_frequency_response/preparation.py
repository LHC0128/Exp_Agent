"""Mx Y RF 频率响应的无硬件预检与安全收尾入口；工作点配置复用 mx_y_rf_sensitivity。"""
from __future__ import annotations

from ...common import load_mapping
from ...lockin_compatibility import require_lockin_compatibility
from ...instrument_config import validate_mapping_usage
from ..mx_y_rf_sensitivity.workflow import safe_shutdown
from .models import MxYRFFrequencyResponseParams
from .run_format import REQUIRED_MAPPING_KEYS


def preflight(params: MxYRFFrequencyResponseParams, root):
    """校验映射、参数与锁相采集能力，返回参数与映射。"""
    validate_mapping_usage(REQUIRED_MAPPING_KEYS, root)
    errors = params.validate(root)
    if errors:
        raise ValueError("；".join(errors))
    mapping = load_mapping(root)
    require_lockin_compatibility(
        "mx-y-rf-frequency-response", params.to_external(), mapping=mapping
    )
    return params, mapping
