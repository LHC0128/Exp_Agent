"""运行时取消与等待。"""
import time
from ...experiment_runtime import check_cancelled

class _RuntimeCancellation:
    @staticmethod
    def raise_if_cancelled() -> None:
        check_cancelled()

def _sleep(seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

def _uncancellable_sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))
