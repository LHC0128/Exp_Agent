"""GUI 分步会话入口。"""
from ...staged_workflow import serve_session
from .models import RfPhaseSensitivityParams
from .workflow import Workflow


def main() -> int:
    serve_session(Workflow, RfPhaseSensitivityParams)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
