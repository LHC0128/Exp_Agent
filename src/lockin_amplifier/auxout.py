"""辅助输出配置模块.

配置 HF2 的辅助输出接口，将解调信号输出到物理接口。
"""

import logging
from typing import Optional

from .instrument import HF2Instrument
from .config import AuxOutConfig

logger = logging.getLogger(__name__)

# 常用 outputselect 值映射
OUTPUT_SELECT_MAP = {
    "X": 0,
    "Y": 1,
    "R": 2,
    "Theta": 3,
}


def configure_aux_output(
    instr: HF2Instrument,
    config: AuxOutConfig,
) -> None:
    """配置辅助输出.

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    config : AuxOutConfig
        辅助输出配置
    """
    aux_path = instr.aux_out_path(config.aux_index)

    # 计算 outputselect 值:
    #   对于解调器 m: outputselect = m * 4 + signal_type
    #   signal_type: 0=X, 1=Y, 2=R, 3=Theta
    output_select = config.demod_index * 4 + config.output_select

    instr.set_int(f"{aux_path}/outputselect", output_select)
    instr.set_double(f"{aux_path}/scale", config.scale)
    instr.set_double(f"{aux_path}/offset", config.offset)
    instr.sync()

    logger.info(
        "辅助输出 %d 已配置: 解调器 %d, 信号=%d, scale=%.3f, offset=%.3f V",
        config.aux_index, config.demod_index,
        config.output_select, config.scale, config.offset,
    )


def set_aux_output_scale(
    instr: HF2Instrument,
    scale: float,
    aux_idx: int = 0,
) -> None:
    """设置辅助输出缩放.

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    scale : float
        缩放系数
    aux_idx : int
        辅助输出索引
    """
    instr.set_double(f"{instr.aux_out_path(aux_idx)}/scale", scale)
    instr.sync()
    logger.info("辅助输出 %d 缩放已设置为 %.3f", aux_idx, scale)


def set_aux_output_offset(
    instr: HF2Instrument,
    offset: float,
    aux_idx: int = 0,
) -> None:
    """设置辅助输出偏置.

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    offset : float
        偏置电压 (V)
    aux_idx : int
        辅助输出索引
    """
    instr.set_double(f"{instr.aux_out_path(aux_idx)}/offset", offset)
    instr.sync()
    logger.info("辅助输出 %d 偏置已设置为 %.3f V", aux_idx, offset)


def read_aux_output_value(
    instr: HF2Instrument,
    aux_idx: int = 0,
) -> float:
    """读取辅助输出当前电压值.

    参数
    ----------
    instr : HF2Instrument
        已连接的仪器实例
    aux_idx : int
        辅助输出索引

    返回
    -------
    float
        当前输出电压 (V)
    """
    value = instr.get_double(f"{instr.aux_out_path(aux_idx)}/value")
    return value
