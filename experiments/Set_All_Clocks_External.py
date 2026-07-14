# %% [markdown] Cell 0
# # 设备时钟源批量同步
#
# 具体设备目标值由 `params/clock_sources.yaml` 管理；本脚本与 GUI 共用
# `lab_workflows.clock_sync`，避免两套实现发生偏差。

# %% Cell 1
from pathlib import Path
import sys

project_root = Path.cwd()
while not (project_root / "params").exists() and project_root.parent != project_root:
    project_root = project_root.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from lab_workflows.clock_sync import synchronize_clocks


# %% Cell 2
def main():
    """按共享配置同步所有设备并打印汇总。"""
    results = synchronize_clocks()
    print("=" * 72)
    print(f"{'设备':18s} {'类型':8s} {'ID':18s} {'之前':6s} {'目标':6s} {'结果':6s}")
    print("-" * 72)
    for result in results:
        status = "成功" if result.ok else "失败"
        print(
            f"{result.label:18s} {result.device_type:8s} {result.device_id:18s} "
            f"{result.before:6s} {result.target:6s} {status:6s}"
        )
        if result.error:
            print(f"  错误: {result.error}")
    succeeded = sum(result.ok for result in results)
    print(f"\n时钟同步完成: {succeeded}/{len(results)} 成功")
    return 0 if succeeded == len(results) else 1


# %% Cell 3
if __name__ == "__main__":
    raise SystemExit(main())
