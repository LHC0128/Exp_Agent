"""仓库级 pytest 配置：限制 `-n auto` 的并行进程数。"""

from __future__ import annotations

import os

# 每个 xdist worker 都会独立导入 numpy/scipy/matplotlib/FastAPI；按满核数启动会占满 CPU，
# 并把依赖真实时间的异步用例拖过超时线。显式 `-n <数量>` 不经过此钩子，照常生效。
AUTO_WORKER_LIMIT = 6


def pytest_xdist_auto_num_workers(config):
    """`-n auto`/`-n logical` 时取 min(核数, 上限)；设置了 PYTEST_XDIST_AUTO_NUM_WORKERS 则交给 xdist 默认处理。"""
    if os.environ.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    return max(1, min(os.cpu_count() or 1, AUTO_WORKER_LIMIT))
