from __future__ import annotations

import re
from typing import Any

DEPENDENCY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEPENDENCY_VERSION = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z]+)*(?:[-+][0-9A-Za-z.-]+)?$")

CORE_DEPENDENCIES = [
    {"name": "matplotlib", "version": "3.10.3"},
    {"name": "numpy", "version": "2.2.6"},
    {"name": "pandas", "version": "2.2.3"},
    {"name": "pillow", "version": "11.2.1"},
    {"name": "pyarrow", "version": "20.0.0"},
    {"name": "scipy", "version": "1.15.3"},
    {"name": "seaborn", "version": "0.13.2"},
]

PROTECTED_DEPENDENCIES = {item["name"] for item in CORE_DEPENDENCIES} | {"echarts", "playwright"}


def normalize_dependencies(values: list[dict[str, Any]]) -> list[dict[str, str]]:
    if len(values) > 32:
        raise ValueError("最多允许 32 个依赖")
    normalized_dependencies = []
    seen = set()
    for item in values:
        name = str(item.get("name", "")).strip()
        version = str(item.get("version", "")).strip()
        normalized_name = re.sub(r"[-_.]+", "-", name).lower()
        _validate_dependency(name, normalized_name, version)
        if normalized_name in seen:
            raise ValueError(f"依赖重复：{name}")
        seen.add(normalized_name)
        normalized_dependencies.append({"name": normalized_name, "version": version})
    return sorted(normalized_dependencies, key=lambda value: value["name"])


def _validate_dependency(name: str, normalized_name: str, version: str) -> None:
    invalid = (
        not DEPENDENCY_NAME.fullmatch(name)
        or (version and not DEPENDENCY_VERSION.fullmatch(version))
        or normalized_name in PROTECTED_DEPENDENCIES
    )
    if invalid:
        raise ValueError(f"不允许的依赖：{name}")
