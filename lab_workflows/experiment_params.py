"""强类型实验参数、外部别名和 GUI schema 生成。"""

from __future__ import annotations

import math
import types
from dataclasses import MISSING, Field, asdict, field, fields
from pathlib import Path
from typing import Any, ClassVar, Literal, Union, get_args, get_origin, get_type_hints

import yaml

from .common import find_project_root, validate_safety_limit


def parameter(
    *,
    default: Any = MISSING,
    default_factory: Any = MISSING,
    external_name: str,
    label: str,
    unit: str = "",
    group: str = "advanced",
    minimum: float | None = None,
    maximum: float | None = None,
    description: str = "",
    visible: bool = True,
    safety_key: str | None = None,
    options: tuple[tuple[Any, str], ...] | None = None,
    options_from_directory: str | None = None,
    options_pattern: str = "*",
) -> Field[Any]:
    """声明模型字段及其稳定外部名称和 GUI 元数据。"""
    metadata = {
        "external_name": external_name,
        "label": label,
        "unit": unit,
        "group": group,
        "minimum": minimum,
        "maximum": maximum,
        "description": description,
        "visible": visible,
        "safety_key": safety_key,
        "options": options,
        "options_from_directory": options_from_directory,
        "options_pattern": options_pattern,
    }
    kwargs: dict[str, Any] = {"metadata": metadata}
    if default is not MISSING:
        kwargs["default"] = default
    if default_factory is not MISSING:
        kwargs["default_factory"] = default_factory
    return field(**kwargs)


def _is_union(origin: Any) -> bool:
    return origin in (Union, types.UnionType)


def _convert_value(name: str, value: Any, annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        if value not in args:
            raise ValueError(f"{name} 必须是 {', '.join(map(str, args))}")
        return value
    if _is_union(origin):
        if value is None and type(None) in args:
            return None
        errors = []
        for item in args:
            if item is type(None):
                continue
            try:
                return _convert_value(name, value, item)
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        raise TypeError(f"{name} 类型错误: {'；'.join(errors)}")
    if origin is list:
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"{name} 必须是数组")
        item_type = args[0] if args else Any
        return [_convert_value(f"{name}[{index}]", item, item_type) for index, item in enumerate(value)]
    if annotation is Any:
        return value
    if annotation is bool:
        if type(value) is not bool:
            raise TypeError(f"{name} 必须是布尔值")
        return value
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not float(value).is_integer():
            raise TypeError(f"{name} 必须是整数")
        return int(value)
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} 必须是数值")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{name} 必须是有限数值")
        return result
    if annotation is str:
        if not isinstance(value, str):
            raise TypeError(f"{name} 必须是字符串")
        return value
    if not isinstance(value, annotation):
        raise TypeError(f"{name} 类型错误，应为 {annotation}")
    return value


def schema_field_type(annotation: Any) -> str:
    """将 Python 类型注解转换为 GUI 支持的 schema 类型。"""
    origin = get_origin(annotation)
    if origin is Literal:
        args = get_args(annotation)
        return schema_field_type(type(args[0])) if args else "string"
    if _is_union(origin):
        concrete = [item for item in get_args(annotation) if item is not type(None)]
        return schema_field_type(concrete[0]) if concrete else "string"
    if origin is list:
        return "array"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    return "string"


class ExperimentParams:
    """所有新模式实验参数模型的共同协议。"""

    schema_version: ClassVar[int] = 1

    @classmethod
    def migrate_external(cls, values: dict[str, Any], schema_version: int) -> dict[str, Any]:
        """迁移旧外部参数；子类可覆盖。"""
        if schema_version > cls.schema_version:
            raise ValueError(
                f"配置 schema_version={schema_version} 高于程序支持版本 {cls.schema_version}"
            )
        return dict(values)

    @classmethod
    def external_names(cls) -> dict[str, str]:
        return {
            str(item.metadata["external_name"]): item.name
            for item in fields(cls)
        }

    @classmethod
    def from_external(
        cls,
        values: dict[str, Any],
        *,
        schema_version: int = 1,
        strict: bool = True,
    ) -> "ExperimentParams":
        migrated = cls.migrate_external(values, schema_version)
        names = cls.external_names()
        unknown = sorted(set(migrated) - set(names))
        if strict and unknown:
            raise ValueError(f"未知参数: {unknown}")
        hints = get_type_hints(cls)
        kwargs = {
            internal: _convert_value(external, migrated[external], hints[internal])
            for external, internal in names.items()
            if external in migrated
        }
        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: Path) -> "ExperimentParams":
        if not path.exists():
            return cls()
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        schema_version = int(payload.get("schema_version", 1))
        values = payload.get("parameters", payload)
        if not isinstance(values, dict):
            raise TypeError(f"参数配置必须是映射: {path}")
        return cls.from_external(values, schema_version=schema_version)

    def to_external(self, *, include_hidden: bool = True) -> dict[str, Any]:
        raw = asdict(self)
        result: dict[str, Any] = {}
        for item in fields(self):
            if include_hidden or bool(item.metadata.get("visible", True)):
                result[str(item.metadata["external_name"])] = raw[item.name]
        return result

    def save_yaml(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "parameters": self.to_external(),
        }
        content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return path

    def validate_model(self) -> list[str]:
        """返回实验特有的交叉字段错误。"""
        return []

    def validate(self, project_root: Path | None = None) -> list[str]:
        root = project_root or find_project_root()
        errors: list[str] = []
        values = asdict(self)
        for item in fields(self):
            value = values[item.name]
            metadata = item.metadata
            external = str(metadata["external_name"])
            minimum = metadata.get("minimum")
            maximum = metadata.get("maximum")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if minimum is not None and value < minimum:
                    errors.append(f"{external}={value} 低于下限 {minimum}")
                if maximum is not None and value > maximum:
                    errors.append(f"{external}={value} 高于上限 {maximum}")
            safety_key = metadata.get("safety_key")
            if safety_key and isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    validate_safety_limit(str(safety_key), float(value))
                except ValueError as exc:
                    errors.append(str(exc))
            options = self._options(item, root)
            if options and value not in [entry[0] for entry in options]:
                errors.append(f"{external} 必须是 {', '.join(str(entry[0]) for entry in options)}")
        errors.extend(self.validate_model())
        return errors

    @staticmethod
    def _options(item: Field[Any], root: Path) -> list[tuple[Any, str]]:
        static = item.metadata.get("options")
        if static is not None:
            return list(static)
        source = item.metadata.get("options_from_directory")
        if not source:
            return []
        directory = (root / str(source)).resolve()
        if directory != root and root not in directory.parents:
            raise ValueError(f"选项目录必须位于项目内: {source}")
        pattern = str(item.metadata.get("options_pattern", "*"))
        return [
            (path.name, path.name)
            for path in sorted(directory.glob(pattern), key=lambda value: value.name.lower())
            if path.is_file()
        ] if directory.is_dir() else []

    def schema(self, project_root: Path | None = None) -> dict[str, Any]:
        root = project_root or find_project_root()
        values = asdict(self)
        hints = get_type_hints(type(self))
        result = []
        for item in fields(self):
            if not bool(item.metadata.get("visible", True)):
                continue
            metadata = item.metadata
            entry = {
                "name": str(metadata["external_name"]),
                "label": str(metadata["label"]),
                "type": schema_field_type(hints[item.name]),
                "default": values[item.name],
                "unit": str(metadata.get("unit", "")),
                "group": str(metadata.get("group", "advanced")),
                "minimum": metadata.get("minimum"),
                "maximum": metadata.get("maximum"),
                "description": str(metadata.get("description", "")),
            }
            options = self._options(item, root)
            if options or metadata.get("options") is not None or metadata.get("options_from_directory"):
                entry["options"] = [
                    {"value": value, "label": label} for value, label in options
                ]
            result.append(entry)
        return {"schema_version": self.schema_version, "fields": result}
