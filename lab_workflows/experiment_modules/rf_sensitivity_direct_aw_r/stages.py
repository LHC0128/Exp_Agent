"""R 响应先采集，再切换频率校相和采集 X 噪声。"""
from dataclasses import replace
from ..rf_sensitivity_direct_aw import stages as shared
from ..rf_sensitivity_direct_aw.stages import FINISH_ID, initial_states, invalidate, invalidated_stages, parameter_impacts, validate_selection
from .run_format import EXPERIMENT_ID

base = {stage.id: stage for stage in shared.ACQUISITION}
ACQUISITION = (
    *(replace(stage, group="基础相位校准") if stage.group == "四级相位校准" else stage for stage in shared.ACQUISITION[:9]),
    replace(base["A10"], title="RF 输入线路与 R 响应频率准备", group="R 响应标定",
            quality_gates=("按实际 Demod0 输入量程检查所选 AuxOut 安全电压范围，越界或缺范围禁止写入",)),
    replace(base["A13"], title="有符号 Z RF 幅度扫描（RF 解调器 R）", group="R 响应标定", dependencies=("A10",),
            parameters=(*base["A13"].parameters, "TEMP_GROUP_SIZE", "SYSTEM_SETTLE_S", "SCAN_POINT_DURATION_S"),
            quality_gates=("每点必须返回有限的 R/X/Y 数据，失败停止并在恢复温控后保存已读点",)),
    replace(base["A12"], title="切换频率与 RF 解调器校相", group="校相与 X 噪声", dependencies=("A13",),
            parameters=("Z_RF_NOISE_FREQ",), display_parameters=("Z_RF_NOISE_FREQ", "Z_RF_AMPLITUDE_INIT", *shared.TEMP)),
    replace(base["A14"], group="校相与 X 噪声", dependencies=("A12",),
            quality_gates=("实际噪声采样率为正、记录至少两点且 X 数据有限，失败保留已取得的诊断记录",)),
    replace(base["A15"], group="收尾与会话",
            quality_gates=("响应配置恢复与安全收尾成功后才标记本轮完成",)),
    replace(base[FINISH_ID], group="收尾与会话"),
)
ANALYSIS = tuple(
    replace(stage, quality_gates=("实验身份与格式匹配、采集已封存；响应点数、场轴、标定及噪声文件清单完整",)) if stage.id == "B01" else
    replace(stage, title="R 绝对色散标定与中心斜率",
            quality_gates=("中心斜率必须为正且有限；绝对色散拟合失败时允许数值梯度估算并关闭线宽补偿",)) if stage.id == "B02" else
    replace(stage, quality_gates=("噪声必须来自快照指定的 RF 解调器 X，至少两点、数据有限且实际采样率为正",)) if stage.id == "B03" else
    replace(stage, artifacts=tuple(name for name in stage.artifacts if not name.startswith("bandwidth_fit.")))
    for stage in shared.ANALYSIS
)


def definition():
    return shared.definition(acquisition=ACQUISITION, analysis=ANALYSIS, experiment_id=EXPERIMENT_ID)
