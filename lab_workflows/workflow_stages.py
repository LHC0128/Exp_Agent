"""工作流阶段描述与显式结束节点的依赖规则，不连接仪器。"""

from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    group: str
    dependencies: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    # 展示归属独立于失效起点；None 表示沿用 parameters。
    display_parameters: tuple[str, ...] | None = None
    quality_gates: tuple[str, ...] = ()


def initial_states(stages):
    return {stage.id: {"status": "pending", "error": ""} for stage in stages}


@dataclass(frozen=True)
class DependencyRules:
    """沿已声明的拓扑顺序计算依赖；结束节点和预检例外属于调用方。"""

    finish_id: str | None
    finish_error: str = "安全结束必须最后执行"
    preflight_id: str = "A01"

    def invalidated_stages(self, stages: Sequence[Stage], stage_id: str) -> tuple[Stage, ...]:
        affected = {stage_id}
        for stage in stages:
            if affected.intersection(stage.dependencies):
                affected.add(stage.id)
        return tuple(stage for stage in stages if stage.id in affected and stage.id != self.finish_id)

    def invalidate(
        self, stages: Sequence[Stage], states: MutableMapping[str, dict[str, Any]], stage_id: str,
    ) -> None:
        for stage in self.invalidated_stages(stages, stage_id):
            states[stage.id] = {
                "status": "pending" if states[stage.id]["status"] == "pending" else "stale", "error": "",
            }

    def parameter_impacts(self, stages: Sequence[Stage]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for stage in stages:
            for key in stage.parameters:
                result.setdefault(key, [])
                result[key] = list(dict.fromkeys([
                    *result[key], *(item.id for item in self.invalidated_stages(stages, stage.id)),
                ]))
        return result

    def validate_selection(
        self, stages: Sequence[Stage], states: Mapping[str, Mapping[str, Any]], selected: Sequence[str],
    ) -> list[str]:
        ids = [stage.id for stage in stages]
        if not selected or len(set(selected)) != len(selected) or any(item not in ids for item in selected):
            raise ValueError("请选择有效且不重复的模块")
        if self.finish_id in selected and selected[-1] != self.finish_id:
            raise ValueError(self.finish_error)
        available = {key for key, value in states.items() if value["status"] == "completed"}
        ordered = [item for item in ids if item in selected]
        for item in ordered:
            stage = stages[ids.index(item)]
            missing = set(stage.dependencies) - available
            if missing:
                raise ValueError(f"{item} {stage.title} 缺少前置模块：" + "、".join(
                    f"{key} {stages[ids.index(key)].title}" for key in ids if key in missing))
            if item != self.preflight_id:
                available.difference_update(stage.id for stage in self.invalidated_stages(stages, item))
            available.add(item)
        return ordered
