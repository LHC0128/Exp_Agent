"""实验内部等待和温度开关步骤。"""
import time
from ...common import CancellationToken, validate_safety_limit
from ...experiment_runtime import check_cancelled
from ...steps import set_temperature_switch
from .models import NoiseSpectrumXYParams

class _RuntimeCancellation(CancellationToken):
    """把既有子进程取消文件接入共享硬件步骤的检查点。"""

    def raise_if_cancelled(self) -> None:
        super().raise_if_cancelled()
        check_cancelled()


def wait(seconds: float) -> None:
    """保留原等待时长，每 0.1 秒检查取消。"""
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        check_cancelled()
        time.sleep(min(0.1, remaining))


def set_temp_switch(dg_temp, enabled, params: NoiseSpectrumXYParams, limits, *, restore_wait=False):
    """本实验测量时关闭物理输出；恢复开启电平后可等待温控恢复。"""
    channel = 2  # 路由设备使用逻辑通道，实际端点由映射解析。
    if not enabled:
        validate_safety_limit("Temp_Switch", 0.0, limits)
        dg_temp.set_output(False, channel=channel)
        return
    validate_safety_limit("Temp_Switch", params.temp_switch, limits)
    set_temperature_switch(
        dg_temp,
        True,
        channel=channel,
        on_voltage=params.temp_switch,
    )
    if restore_wait:
        wait(params.temp_switch_on_settle_s)


