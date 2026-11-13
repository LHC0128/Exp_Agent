"""锁存信号源同步输出：先使能同步，再用一次主输出 OFF→ON 边沿起振。

DG4000 真机（DG4162 固件 00.01.14 受控实验）：输出已开启时写 ``SYNC:STATe ON`` 只更新
状态位——面板显示同步已开、SCPI 回读 ON，但同步口没有信号（CH3 实测仅 0.13 V 噪声）。
同步发生器只在「主输出 OFF→ON 边沿、且边沿时同步已使能」时起振，补上边沿后立即出现
0→3.5 V TTL；边沿与写 ON 之间是否等待无影响。

``APPLy`` 重绘波形既不产生也不破坏已锁存的同步：一次锁存后换频、换脉宽都保持，因此每个
扫描段只需在首帧锁存一次。该锁存本身偶发失败（真机多轮 4/6、5/6、4/4 波动），调用方应
用同帧实测同步幅度判定，缺失时重新锁存并重采本帧，不要把状态位当判据。
"""
from __future__ import annotations

from typing import Callable


def arm_sync_output(signal_generator, *, settle_wait: Callable[[float], None] | None = None) -> None:
    """在非 DC 波形上锁存信号源同步输出。

    Parameters
    ----------
    signal_generator
        已完成设定波形、且主输出允许开启的信号源驱动（只用 set_output / set_sync_state）。
    settle_wait
        可选等待函数，在开同步与开输出之间插入；真机零延时与 0.3 s 等效，一般不需要。
    """
    signal_generator.set_output(False)
    signal_generator.set_sync_state(True)
    if settle_wait is not None:
        settle_wait(0.05)
    signal_generator.set_output(True)
