"""独立运行身份；复用格式校验，禁止混用 X 标定历史。"""
from functools import partial
from ..rf_sensitivity_direct_aw import run_format as shared

EXPERIMENT_ID = "rf-sensitivity-aw-r"
DATA_TYPE = "RF_Field_Sensitivity_AW_R"
RESPONSE_COMPONENT = "R"
ACQUISITION_METHOD = "direct_aw_r"
SIGNAL_CHAIN = "Demod0 Y -> AuxOut2 -> Signal Input2 -> Demod3 R calibration / X noise"
COMPARISON_JOB_KIND = f"comparison:{EXPERIMENT_ID}"
COMPARISON_FILES = shared.COMPARISON_FILES
is_new_run = partial(shared.is_new_run, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD, schema_versions=(2, 3))
analysis_block_reason = partial(shared.analysis_block_reason, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD, schema_versions=(2, 3))
comparison_block_reason = partial(shared.comparison_block_reason, experiment_id=EXPERIMENT_ID, acquisition_method=ACQUISITION_METHOD, schema_versions=(2, 3))
normalize_comparison_runs = shared.normalize_comparison_runs
