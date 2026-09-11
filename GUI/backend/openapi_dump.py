"""导出 OpenAPI schema，供 openapi-typescript 生成前端 API 类型。"""

import json
import sys
from pathlib import Path

from .main import app


def main(output: Path | None = None) -> int:
    payload = json.dumps(app.openapi(), ensure_ascii=False, indent=2)
    if output is None:
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
        return 0
    output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    raise SystemExit(main(target))
