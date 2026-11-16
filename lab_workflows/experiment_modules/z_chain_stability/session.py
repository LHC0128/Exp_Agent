"""ZChainStability 的 GUI 分步会话子进程入口；EOF、取消和异常均进入统一收尾。"""

from lab_workflows.staged_workflow import serve_session

from .models import ZChainStabilityParams
from .workflow import Workflow

if __name__ == "__main__":
    serve_session(Workflow, ZChainStabilityParams)
