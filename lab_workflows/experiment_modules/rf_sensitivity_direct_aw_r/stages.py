"""R 响应先采集，再切换频率校相和采集 X 噪声。"""
from dataclasses import replace
from ..rf_sensitivity_direct_aw import stages as shared
from ..rf_sensitivity_direct_aw.stages import FINISH_ID, initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection
from .run_format import EXPERIMENT_ID

base = {stage.id: stage for stage in shared.ACQUISITION}
ACQUISITION = (
    *(replace(stage, group="基础相位校准") if stage.group == "四级相位校准" else stage for stage in shared.ACQUISITION[:9]),
    replace(base["A10"], title="RF 输入线路与 R 响应频率准备", group="R 响应标定"),
    replace(base["A13"], title="有符号 Z RF 幅度扫描（RF 解调器 R）", group="R 响应标定", dependencies=("A10",),
            parameters=(*base["A13"].parameters, "TEMP_GROUP_SIZE", "SYSTEM_SETTLE_S", "SCAN_POINT_DURATION_S")),
    replace(base["A12"], title="切换频率与 RF 解调器校相", group="校相与 X 噪声", dependencies=("A13",),
            parameters=("Z_RF_NOISE_FREQ",), display_parameters=("Z_RF_NOISE_FREQ", "Z_RF_AMPLITUDE_INIT", *shared.TEMP)),
    replace(base["A14"], group="校相与 X 噪声", dependencies=("A12",)),
    replace(base["A15"], group="收尾与会话"),
    replace(base[FINISH_ID], group="收尾与会话"),
)
ANALYSIS = tuple(replace(stage, title="R 绝对色散标定与中心斜率") if stage.id == "B02" else stage for stage in shared.ANALYSIS)


def definition():
    return shared.definition(acquisition=ACQUISITION, analysis=ANALYSIS, experiment_id=EXPERIMENT_ID)
