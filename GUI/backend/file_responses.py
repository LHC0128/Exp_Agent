"""运行产物的文件响应：图片与 PDF 供浏览器内预览，其他类型保持下载。"""

import mimetypes
from pathlib import Path

from fastapi.responses import FileResponse


# 只有这些类型允许内联展示；HTML、SVG 等可执行内容仍走附件下载，避免在应用源下执行脚本。
INLINE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def artifact_response(path: Path) -> FileResponse:
    """图片和 PDF 返回 inline 供预览，其余类型返回 attachment 下载。"""
    suffix = path.suffix.lower()
    if suffix in INLINE_MEDIA_TYPES:
        # 以系统 mimetypes 为准；Windows 注册表缺少 webp 等映射时回退白名单类型。
        media_type = mimetypes.guess_type(path.name)[0] or INLINE_MEDIA_TYPES[suffix]
        return FileResponse(path, media_type=media_type, filename=path.name,
                            content_disposition_type="inline")
    return FileResponse(path, filename=path.name)
