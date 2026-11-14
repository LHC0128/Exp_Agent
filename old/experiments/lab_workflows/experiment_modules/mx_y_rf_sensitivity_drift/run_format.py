"""长飘父清单的身份与格式资格；与参数版本、子原始格式分别管理。"""
import math
from ..mx_y_rf_sensitivity.run_format import REQUIRED_MAPPING_KEYS

EXPERIMENT_ID = "mx-y-rf-sensitivity-drift"
DATA_TYPE = "Mx_Y_RF_Sensitivity_Drift"
CHILD_EXPERIMENT_ID = "mx-y-rf-sensitivity"
CHILD_DATA_TYPE = "Mx_Y_RF_Sensitivity"
RAW_FORMAT_VERSION = 1
MANIFEST = "raw/drift_manifest.yaml"
RECORD_STATUSES = {"pending", "running", "completed", "invalid", "invalid_quality",
    "analysis_failed", "acquisition_failed", "cancelled"}


def format_block_reason(config, manifest=None):
    """历史展示可读取合法运行中的身份，分析仍须单独检查封存状态。"""
    return analysis_block_reason(config, manifest, allow_running=True)


def analysis_block_reason(config, manifest=None, *, allow_running=False):
    if not isinstance(config, dict) or not isinstance(config.get("parameters"), dict):
        return "缺少有效运行参数快照"
    if config.get("experiment_id") != EXPERIMENT_ID or config.get("data_type") != DATA_TYPE:
        return "运行目录不属于 Mx Y RF 灵敏度长飘"
    if config.get("child_experiment_id") != CHILD_EXPERIMENT_ID or config.get("manifest") != MANIFEST:
        return "父运行缺少标准子实验身份或清单路径"
    if config.get("scan_mode") != "repeated_measurement":
        return "父运行不是已验证的重复测量格式"
    version = config.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该父清单原始数据格式版本"
    if not allow_running and config.get("completion_status") in ("running", "acquired"):
        return "父采集仍在执行，请先完成安全收尾"
    if manifest is None:
        return ""
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        return "不支持该轮次清单结构"
    version = manifest.get("raw_format_version")
    if version is not None and (type(version) is not int or version != RAW_FORMAT_VERSION):
        return "不支持该轮次清单原始数据格式版本"
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        return "轮次清单缺少计划记录"
    previous, previous_index = -1.0, -1
    marked = config.get("raw_format_version") is not None or manifest.get("raw_format_version") is not None
    for index, record in enumerate(records):
        if not isinstance(record, dict) or type(record.get("cycle_index")) is not int:
            return "轮次索引必须为整数"
        cycle_index = record["cycle_index"]
        if cycle_index <= previous_index or (index == 0 and cycle_index != 0) or (marked and cycle_index != index):
            return "当前格式轮次索引必须从零连续递增；已验证的旧清单允许递增稀疏索引"
        offset = record.get("scheduled_offset_s")
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0 or offset <= previous:
            return "计划偏移必须为非负、有限且严格递增的秒数"
        if record.get("status") not in RECORD_STATUSES:
            return "轮次状态不属于已验证的格式"
        if not all(key in record for key in ("child_run_dir", "actual_start_utc", "start_lag_s", "analysis", "error")):
            return "轮次清单缺少历史必需字段"
        previous, previous_index = offset, cycle_index
    # 旧采集已明确保存上述身份、扫描模式及 schema=1 清单，不猜测任意缺标记目录。
    return ""
