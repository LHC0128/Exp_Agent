"""导出 OpenAPI schema，供 openapi-typescript 生成前端 API 类型。"""

import json

from .main import app

if __name__ == "__main__":
    print(json.dumps(app.openapi(), ensure_ascii=False, indent=2))
