"""实验中心标签及实验归类的持久化配置。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..common import find_project_root


DEFAULT_TAGS = (
    {"id": "measurement", "label": "测量"},
    {"id": "calibration", "label": "标定"},
    {"id": "optimization", "label": "优化"},
    {"id": "verification", "label": "硬件验证"},
)

PARAMETER_GROUPS = ("basic", "advanced")


def catalog_path() -> Path:
    return find_project_root() / "params" / "experiment_catalog.yaml"


def _normalize(
    payload: dict[str, Any] | None,
    default_categories: dict[str, str],
) -> dict[str, Any]:
    payload = payload or {}
    raw_tags = payload.get("tags") or list(DEFAULT_TAGS)
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_tags:
        tag_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()
        if tag_id and label and tag_id not in seen:
            tags.append({"id": tag_id, "label": label})
            seen.add(tag_id)
    for item in DEFAULT_TAGS:
        if item["id"] not in seen:
            tags.append(dict(item))
            seen.add(item["id"])
    assignments = {
        experiment_id: str(tag_id)
        for experiment_id, tag_id in (payload.get("assignments") or {}).items()
        if experiment_id in default_categories and str(tag_id) in seen
    }
    experiment_descriptions = {
        experiment_id: str(description).strip()
        for experiment_id, description in (payload.get("experiment_descriptions") or {}).items()
        if experiment_id in default_categories and len(str(description).strip()) <= 500
    }
    experiment_titles = {
        experiment_id: str(title).strip()
        for experiment_id, title in (payload.get("experiment_titles") or {}).items()
        if experiment_id in default_categories
        and 0 < len(str(title).strip()) <= 80
    }
    parameter_layouts = {
        experiment_id: {
            group: [str(name) for name in layout.get(group, []) if str(name)]
            if isinstance(layout.get(group), list)
            else []
            for group in PARAMETER_GROUPS
        }
        for experiment_id, layout in (payload.get("parameter_layouts") or {}).items()
        if experiment_id in default_categories and isinstance(layout, dict)
    }
    return {
        "schema_version": 2,
        "tags": tags,
        "assignments": assignments,
        "experiment_descriptions": experiment_descriptions,
        "experiment_titles": experiment_titles,
        "parameter_layouts": parameter_layouts,
    }


def _normalize_parameter_layout(
    layout: dict[str, Any] | None,
    fields: list[dict[str, Any]],
    *,
    strict: bool,
) -> dict[str, list[str]]:
    """按当前 schema 规范化参数分组；严格模式用于保存前校验。"""
    layout = layout or {}
    field_names = [str(field["name"]) for field in fields]
    valid_names = set(field_names)
    normalized = {group: [] for group in PARAMETER_GROUPS}
    used: set[str] = set()
    unknown: set[str] = set()
    duplicates: set[str] = set()

    for group in PARAMETER_GROUPS:
        raw_names = layout.get(group, [])
        if strict and not isinstance(raw_names, list):
            raise ValueError(f"{group} 参数分类必须是列表")
        if not isinstance(raw_names, list):
            continue
        for raw_name in raw_names:
            name = str(raw_name)
            if name not in valid_names:
                unknown.add(name)
                continue
            if name in used:
                duplicates.add(name)
                continue
            normalized[group].append(name)
            used.add(name)

    if strict:
        extra_groups = sorted(set(layout) - set(PARAMETER_GROUPS))
        missing = sorted(valid_names - used)
        if extra_groups:
            raise ValueError(f"未知参数分类: {extra_groups}")
        if unknown:
            raise ValueError(f"分类中包含未知参数: {sorted(unknown)}")
        if duplicates:
            raise ValueError(f"参数重复出现在分类中: {sorted(duplicates)}")
        if missing:
            raise ValueError(f"参数未归入基础或高级分类: {missing}")
        return normalized

    for field in fields:
        name = str(field["name"])
        if name in used:
            continue
        group = "basic" if field.get("group") == "basic" else "advanced"
        normalized[group].append(name)
    return normalized


def validate_parameter_layout(
    layout: dict[str, Any],
    fields: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """校验前端提交的完整参数布局并返回规范化副本。"""
    return _normalize_parameter_layout(layout, fields, strict=True)


def apply_parameter_layout(
    experiment_id: str,
    schema: dict[str, Any],
    default_categories: dict[str, str],
) -> dict[str, Any]:
    """将已保存的基础/高级分类及顺序合并到实验 schema。"""
    fields = list(schema.get("fields") or [])
    saved = load_catalog(default_categories)["parameter_layouts"].get(experiment_id)
    if not saved:
        return {**schema, "parameter_layout_saved": False}
    layout = _normalize_parameter_layout(saved, fields, strict=False)
    fields_by_name = {str(field["name"]): field for field in fields}
    merged_fields = []
    for group in PARAMETER_GROUPS:
        for name in layout[group]:
            merged_fields.append({**fields_by_name[name], "group": group})
    return {
        **schema,
        "fields": merged_fields,
        "parameter_layout": layout,
        "parameter_layout_saved": True,
    }


def set_parameter_layout(
    experiment_id: str,
    layout: dict[str, Any],
    fields: list[dict[str, Any]],
    default_categories: dict[str, str],
) -> None:
    """保存单个实验的基础/高级参数分类及组内顺序。"""
    if experiment_id not in default_categories:
        raise KeyError(f"未知实验: {experiment_id}")
    normalized = validate_parameter_layout(layout, fields)
    catalog = load_catalog(default_categories)
    catalog["parameter_layouts"][experiment_id] = normalized
    save_catalog(catalog, default_categories)


def load_catalog(default_categories: dict[str, str]) -> dict[str, Any]:
    path = catalog_path()
    payload = None
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    return _normalize(payload, default_categories)


def save_catalog(catalog: dict[str, Any], default_categories: dict[str, str]) -> None:
    normalized = _normalize(catalog, default_categories)
    path = catalog_path()
    temporary = path.with_suffix(".yaml.tmp")
    content = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False)
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except PermissionError:
        temporary.unlink(missing_ok=True)
        path.write_text(content, encoding="utf-8")


def public_catalog(default_categories: dict[str, str]) -> dict[str, Any]:
    catalog = load_catalog(default_categories)
    effective = {
        experiment_id: catalog["assignments"].get(experiment_id, category)
        for experiment_id, category in default_categories.items()
    }
    return {**catalog, "assignments": effective}


def add_tag(
    label: str,
    default_categories: dict[str, str],
) -> dict[str, str]:
    label = label.strip()
    if not label:
        raise ValueError("标签名称不能为空")
    if len(label) > 30:
        raise ValueError("标签名称不能超过 30 个字符")
    catalog = load_catalog(default_categories)
    if any(item["label"].casefold() == label.casefold() for item in catalog["tags"]):
        raise ValueError("标签名称已经存在")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    tag_id = slug or f"tag-{uuid4().hex[:8]}"
    existing = {item["id"] for item in catalog["tags"]}
    if tag_id in existing:
        tag_id = f"{tag_id}-{uuid4().hex[:6]}"
    tag = {"id": tag_id, "label": label}
    catalog["tags"].append(tag)
    save_catalog(catalog, default_categories)
    return tag


def rename_tag(
    tag_id: str,
    label: str,
    default_categories: dict[str, str],
) -> dict[str, str]:
    label = label.strip()
    if not label:
        raise ValueError("标签名称不能为空")
    if len(label) > 30:
        raise ValueError("标签名称不能超过 30 个字符")
    catalog = load_catalog(default_categories)
    if any(item["id"] != tag_id and item["label"].casefold() == label.casefold() for item in catalog["tags"]):
        raise ValueError("标签名称已经存在")
    for tag in catalog["tags"]:
        if tag["id"] == tag_id:
            tag["label"] = label
            save_catalog(catalog, default_categories)
            return tag
    raise KeyError(f"未知标签: {tag_id}")


def assign_tag(
    experiment_id: str,
    tag_id: str,
    default_categories: dict[str, str],
) -> None:
    if experiment_id not in default_categories:
        raise KeyError(f"未知实验: {experiment_id}")
    catalog = load_catalog(default_categories)
    if tag_id not in {item["id"] for item in catalog["tags"]}:
        raise KeyError(f"未知标签: {tag_id}")
    catalog["assignments"][experiment_id] = tag_id
    save_catalog(catalog, default_categories)


def set_experiment_description(
    experiment_id: str,
    description: str,
    default_categories: dict[str, str],
) -> None:
    """保存单个实验在 GUI 中显示的介绍。"""
    if experiment_id not in default_categories:
        raise KeyError(f"未知实验: {experiment_id}")
    description = description.strip()
    if len(description) > 500:
        raise ValueError("实验介绍不能超过 500 个字符")
    catalog = load_catalog(default_categories)
    catalog["experiment_descriptions"][experiment_id] = description
    save_catalog(catalog, default_categories)


def set_experiment_metadata(
    experiment_id: str,
    title: str,
    description: str,
    default_categories: dict[str, str],
) -> None:
    """保存单个实验在 GUI 中显示的名称和介绍。"""
    if experiment_id not in default_categories:
        raise KeyError(f"未知实验: {experiment_id}")
    title = title.strip()
    description = description.strip()
    if not title:
        raise ValueError("实验名称不能为空")
    if len(title) > 80:
        raise ValueError("实验名称不能超过 80 个字符")
    if len(description) > 500:
        raise ValueError("实验介绍不能超过 500 个字符")
    catalog = load_catalog(default_categories)
    catalog["experiment_titles"][experiment_id] = title
    catalog["experiment_descriptions"][experiment_id] = description
    save_catalog(catalog, default_categories)
