"""GUI 分步会话子进程入口；EOF、取消和异常均进入本实验的统一收尾。"""
from lab_workflows.staged_workflow import serve_session

from .models import CoilConstantCalibrationParams
from .workflow import Workflow


def main() -> None:
    serve_session(Workflow, CoilConstantCalibrationParams)


if __name__ == "__main__":
    main()
