"""工作流状态读写的旧导入兼容入口；新代码使用 lab_workflows.workflow_state。"""

from lab_workflows.workflow_state import read_state, write_state

__all__ = ["read_state", "write_state"]
