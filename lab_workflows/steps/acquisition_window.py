"""采集窗口：先恢复温控，再保存当前点或当前组的内存数据。"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


@contextmanager
def acquisition_window(set_enabled: Callable[[bool], None], wait: Callable[[float], None],
                       system_settle_s: float):
    """配置由调用方提前完成；关闭失败和取消同样尝试恢复温控。"""
    if isinstance(system_settle_s, bool) or not math.isfinite(system_settle_s) or system_settle_s < 0:
        raise ValueError("系统稳定时间必须是有限非负秒数")
    failure = None
    try:
        set_enabled(False)
        wait(system_settle_s)
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            set_enabled(True)
        except BaseException:
            if failure is None:
                raise
            logger.exception("恢复温控失败；保留原始采集错误")


class DeferredAcquisitionWrites:
    """仅缓存当前采集组的保存参数；退出前调用方必须先尝试恢复温控。"""

    def __init__(self, restore: Callable[[], None] | None = None):
        self._pending: list[tuple[Callable[..., Any], tuple, dict]] = []
        self._restore = restore

    def __enter__(self):
        return self

    def add(self, writer: Callable[..., Any], *args, **kwargs) -> None:
        # 扫描累计数组后续会原地更新，必须冻结本次数据而非保存其引用。
        self._pending.append((writer, deepcopy(args), deepcopy(kwargs)))

    def flush(self) -> None:
        pending, self._pending = self._pending, []
        failure = None
        for writer, args, kwargs in pending:
            try:
                writer(*args, **kwargs)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                logger.exception("采集数据保存失败")
        if failure is not None:
            raise failure

    def __exit__(self, kind, error, traceback):
        restore_error = None
        if self._restore is not None:
            try:
                self._restore()
            except BaseException as exc:
                restore_error = exc
                logger.exception("恢复温控失败，停止后续采集并抢救内存数据")
        try:
            self.flush()
        except BaseException:
            if error is None and restore_error is None:
                raise
            logger.exception("部分数据保存失败；保留原始采集错误")
        if error is None and restore_error is not None:
            raise restore_error
        return False
