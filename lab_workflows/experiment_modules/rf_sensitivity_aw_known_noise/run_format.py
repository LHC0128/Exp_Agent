"""同一噪声配置下的控制子运行与聚合运行资格。"""
from functools import partial
from ..rf_sensitivity_direct_aw import run_format as shared

EXPERIMENT_ID = "rf-sensitivity-aw-known-noise"
DATA_TYPE = "RF_Field_Sensitivity_AW_Known_Noise"
SIGNAL_CHAIN = shared.SIGNAL_CHAIN
RESPONSE_COMPONENT = "X"
ACQUISITION_METHOD = "direct_aw_known_noise"
COMPARISON_JOB_KIND = f"comparison:{EXPERIMENT_ID}"
COMPARISON_FILES = ()
# v4 取消手动噪声幅度参数，响应和噪声原始记录仍沿用既有格式。
SUPPORTED_SCHEMA_VERSIONS = (2, 3, 4)
is_new_run = partial(shared.is_new_run, experiment_id=EXPERIMENT_ID,
    acquisition_method=ACQUISITION_METHOD, schema_versions=SUPPORTED_SCHEMA_VERSIONS)
analysis_block_reason = partial(shared.analysis_block_reason, experiment_id=EXPERIMENT_ID,
    acquisition_method=ACQUISITION_METHOD, schema_versions=SUPPORTED_SCHEMA_VERSIONS)


def comparison_block_reason(directory, config):
    return "本实验在一次运行内比较所选控制版本"
