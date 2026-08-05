"""Explicit aggregate that loads every ORM model into one metadata registry."""

from importlib import import_module

from app.infrastructure.db.model_base import Base

MODEL_MODULES = (
    "conversations",
    "evolution",
    "executions",
    "memory",
    "permissions",
    "plans",
    "runs",
    "scheduling",
    "skills",
    "workspaces",
)

for module_name in MODEL_MODULES:
    import_module(f"app.infrastructure.db.models.{module_name}")

metadata = Base.metadata

__all__ = ["MODEL_MODULES", "metadata"]
