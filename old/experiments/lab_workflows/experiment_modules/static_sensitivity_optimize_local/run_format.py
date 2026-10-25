"""批次身份与原始格式；旧 RAMP 批次只读取已有单点结论。"""
from pathlib import Path
import yaml

EXPERIMENT_ID = "static-sensitivity-optimize-local"
DATA_TYPE = "Static_Magnetic_Field_Sensitivity"
RAW_FORMAT_VERSION = 1
MANIFEST = "raw/optimization_manifest.json"
LEGACY_SUMMARY = "optimization_local_summary.yaml"


def read_config(path):
    run_dir = Path(path)
    if run_dir.name == "experiment_config.yaml":
        run_dir = run_dir.parent
    try:
        path = run_dir / "experiment_config.yaml"
        if path.is_file():
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            payload = yaml.safe_load((run_dir / LEGACY_SUMMARY).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not {"batch_timestamp", "baseline_params", "optimize_sequence", "decision", "results"}.issubset(payload):
                return None
            config = dict(experiment_id=EXPERIMENT_ID, raw_format_version=0,
                acquisition_method="legacy_local_summary", completion_status="completed",
                timestamp=str(payload["batch_timestamp"]), run_tag=run_dir.name.removeprefix(str(payload["batch_timestamp"]) + "_"),
                parameters={"BASELINE_PARAMS": payload["baseline_params"], "OPTIMIZE_SEQUENCE": payload["optimize_sequence"]})
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return config


def format_block_reason(config):
    if not isinstance(config, dict) or config.get("experiment_id") != EXPERIMENT_ID:
        return "运行目录与局部优化批次不匹配"
    version = config.get("raw_format_version")
    method = config.get("acquisition_method")
    if type(version) is not int or (version, method) not in ((1, "dc_step_local"), (0, "legacy_local_summary")):
        return "不支持的局部优化原始格式"
    if not isinstance(config.get("parameters"), dict):
        return "批次缺少参数快照"
    return None


def analysis_block_reason(config):
    if reason := format_block_reason(config):
        return reason
    if config.get("completion_status") in ("running", "acquired"):
        return "批次仍在写入，请先安全封存或结束会话"
    return None
