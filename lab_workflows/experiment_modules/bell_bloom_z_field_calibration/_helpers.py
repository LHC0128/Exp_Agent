"""可取消等待与温控恢复等待。"""
import time
from ...experiment_runtime import check_cancelled


class RuntimeCancellation:
    """将现有公共温控步骤接到 GUI 取消文件。"""

    @staticmethod
    def raise_if_cancelled():
        check_cancelled()


def sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def recovery_wait(seconds):
    time.sleep(seconds)
