"""实验默认参数的基线层与本机覆盖层。

基线 ``params/experiments/<id>.yaml`` 随代码进 Git，是唯一权威默认值；
本机覆盖 ``params/experiments/<id>.local.yaml`` 只存在于本机、不进版本库，
用于现场调参而不必发布。覆盖文件只保存与基线不同的键，合并后仍交给各实验
的参数模型做类型转换、校验与安全限值检查。

兼容脚本的基线来自脚本内字面量与 YAML 两处，因此各方法都接受显式的
``baseline`` 取值；不传时才回落到基线文件本身。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

OVERRIDE_SUFFIX = ".local.yaml"


def override_path(baseline: Path) -> Path:
    """返回与基线同目录的本机覆盖文件路径。"""
    return Path(baseline).with_suffix(OVERRIDE_SUFFIX)


def _read_parameters(path: Path) -> dict[str, Any]:
    """读取参数文件的 ``parameters`` 段；文件不存在时返回空映射。"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    values = payload.get("parameters", payload)
    if not isinstance(values, dict):
        raise TypeError(f"参数配置必须是映射: {path}")
    return dict(values)


def _write_parameters(path: Path, values: Mapping[str, Any], schema_version: int) -> None:
    """原子写入参数文件，先写临时文件再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": schema_version, "parameters": dict(values)}
    content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


class ParamLayers:
    """一组实验默认参数的两层视图：基线 + 本机覆盖。

    覆盖层只保存与基线不同的键，因此删除覆盖文件即可回到基线；``promote``
    把合并结果写回基线并删除覆盖层。
    """

    def __init__(self, baseline: Path) -> None:
        self.baseline = Path(baseline)
        self.override = override_path(self.baseline)

    def schema_version(self) -> int:
        if not self.baseline.exists():
            return 1
        with self.baseline.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        return int(payload.get("schema_version", 1))

    def baseline_values(self) -> dict[str, Any]:
        return _read_parameters(self.baseline)

    def override_values(self) -> dict[str, Any]:
        return _read_parameters(self.override)

    def merged(self, baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """基线取值叠加本机覆盖，供参数模型转换。"""
        values = dict(self.baseline_values() if baseline is None else baseline)
        values.update(self.override_values())
        return values

    def overridden_keys(self, baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """返回真正改变取值的覆盖键；仅用于展示，不参与加载。"""
        current = self.baseline_values() if baseline is None else dict(baseline)
        return {
            key: value
            for key, value in self.override_values().items()
            if current.get(key) != value
        }

    def has_override(self, baseline: Mapping[str, Any] | None = None) -> bool:
        return bool(self.overridden_keys(baseline))

    def write_override(
        self, values: Mapping[str, Any], *, baseline: Mapping[str, Any] | None = None
    ) -> Path:
        """把与基线不同的键写入覆盖层；没有差异时删除覆盖文件。"""
        current = self.baseline_values() if baseline is None else dict(baseline)
        delta = {key: value for key, value in values.items() if current.get(key) != value}
        if not delta:
            self.discard()
            return self.override
        _write_parameters(self.override, delta, self.schema_version())
        return self.override

    def promote(self, values: Mapping[str, Any]) -> None:
        """把完整取值写回基线并删除覆盖层。"""
        _write_parameters(self.baseline, values, self.schema_version())
        self.discard()

    def discard(self) -> None:
        if self.override.exists():
            self.override.unlink()
