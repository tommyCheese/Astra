"""Produce a deterministic structural inventory for the Astra backend.

The analyzer intentionally depends only on the Python standard library so local
development and CI use exactly the same definition of modules, functions,
complexity, public symbols, and imports.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FunctionMetric:
    module: str
    qualified_name: str
    line: int
    lines: int
    complexity: int


@dataclass(frozen=True)
class ModuleMetric:
    module: str
    path: str
    lines: int
    public_symbols: tuple[str, ...]
    internal_imports: tuple[str, ...]


@dataclass(frozen=True)
class ArchitectureInventory:
    source_root: str
    production_lines: int
    module_count: int
    class_count: int
    public_symbol_count: int
    modules: tuple[ModuleMetric, ...]
    functions: tuple[FunctionMetric, ...]


class ComplexityVisitor(ast.NodeVisitor):
    """Calculate a small, documented cyclomatic-complexity approximation."""

    def __init__(self) -> None:
        self.complexity = 1

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.complexity += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.complexity += max(0, len(node.cases) - 1)
        self.generic_visit(node)


def module_name(source_root: Path, source_file: Path) -> str:
    relative_path = source_file.relative_to(source_root.parent).with_suffix("")
    parts = list(relative_path.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def source_line_count(source_file: Path) -> int:
    with source_file.open(encoding="utf-8") as source:
        return sum(1 for _ in source)


def public_symbols(module_tree: ast.Module) -> tuple[str, ...]:
    explicit_exports = next(
        (
            assignment.value
            for assignment in module_tree.body
            if isinstance(assignment, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "__all__" for target in assignment.targets)
        ),
        None,
    )
    if isinstance(explicit_exports, (ast.List, ast.Tuple)):
        names = [
            element.value
            for element in explicit_exports.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        return tuple(sorted(names))

    names = [
        statement.name
        for statement in module_tree.body
        if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and not statement.name.startswith("_")
    ]
    return tuple(sorted(names))


def resolve_relative_import(current_module: str, imported_module: str | None, level: int) -> str:
    if level == 0:
        return imported_module or ""
    package_parts = current_module.split(".")[:-1]
    retained_parts = package_parts[: max(0, len(package_parts) - level + 1)]
    if imported_module:
        retained_parts.extend(imported_module.split("."))
    return ".".join(retained_parts)


class RuntimeImportCollector(ast.NodeVisitor):
    """Collect imports that execute at runtime, excluding type-checker-only edges."""

    def __init__(self, current_module: str) -> None:
        self.current_module = current_module
        self.imports: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for statement in node.orelse:
                self.visit(statement)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(
            alias.name
            for alias in node.names
            if alias.name == "app" or alias.name.startswith("app.")
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        imported_module = resolve_relative_import(
            self.current_module,
            node.module,
            node.level,
        )
        if imported_module == "app" or imported_module.startswith("app."):
            self.imports.add(imported_module)


def internal_imports(module_tree: ast.Module, current_module: str) -> tuple[str, ...]:
    collector = RuntimeImportCollector(current_module)
    collector.visit(module_tree)
    return tuple(sorted(collector.imports))


def decision_complexity(function_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    visitor = ComplexityVisitor()
    for statement in function_node.body:
        visitor.visit(statement)
    return visitor.complexity


def iter_functions(
    nodes: Sequence[ast.stmt],
    *,
    current_module: str,
    parents: tuple[str, ...] = (),
) -> Iterable[FunctionMetric]:
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified_name = ".".join((*parents, node.name))
            yield FunctionMetric(
                module=current_module,
                qualified_name=qualified_name,
                line=node.lineno,
                lines=(node.end_lineno or node.lineno) - node.lineno + 1,
                complexity=decision_complexity(node),
            )
            yield from iter_functions(
                node.body,
                current_module=current_module,
                parents=(*parents, node.name),
            )
        elif isinstance(node, ast.ClassDef):
            yield from iter_functions(
                node.body,
                current_module=current_module,
                parents=(*parents, node.name),
            )


def build_inventory(source_root: Path) -> ArchitectureInventory:
    resolved_root = source_root.resolve()
    modules: list[ModuleMetric] = []
    functions: list[FunctionMetric] = []
    class_count = 0

    for source_file in sorted(resolved_root.rglob("*.py")):
        source_text = source_file.read_text(encoding="utf-8")
        module_tree = ast.parse(source_text, filename=str(source_file))
        class_count += sum(isinstance(node, ast.ClassDef) for node in ast.walk(module_tree))
        current_module = module_name(resolved_root, source_file)
        modules.append(
            ModuleMetric(
                module=current_module,
                path=str(source_file.relative_to(resolved_root.parent)),
                lines=source_line_count(source_file),
                public_symbols=public_symbols(module_tree),
                internal_imports=internal_imports(module_tree, current_module),
            )
        )
        functions.extend(iter_functions(module_tree.body, current_module=current_module))

    modules.sort(key=lambda metric: metric.module)
    functions.sort(key=lambda metric: (metric.module, metric.line, metric.qualified_name))
    return ArchitectureInventory(
        source_root=str(resolved_root),
        production_lines=sum(module.lines for module in modules),
        module_count=len(modules),
        class_count=class_count,
        public_symbol_count=sum(len(module.public_symbols) for module in modules),
        modules=tuple(modules),
        functions=tuple(functions),
    )


def render_markdown(inventory: ArchitectureInventory, *, limit: int) -> str:
    largest_modules = sorted(inventory.modules, key=lambda metric: (-metric.lines, metric.module))[:limit]
    largest_functions = sorted(
        inventory.functions,
        key=lambda metric: (-metric.lines, -metric.complexity, metric.module, metric.line),
    )[:limit]
    most_complex = sorted(
        inventory.functions,
        key=lambda metric: (-metric.complexity, -metric.lines, metric.module, metric.line),
    )[:limit]

    output = [
        "# Backend architecture inventory",
        "",
        f"- Production lines: {inventory.production_lines}",
        f"- Modules: {inventory.module_count}",
        f"- Classes: {inventory.class_count}",
        f"- Public symbols: {inventory.public_symbol_count}",
        f"- Functions and methods: {len(inventory.functions)}",
        "",
        "## Largest modules",
        "",
        "| Lines | Module | Path | Public symbols | Internal imports |",
        "| ---: | --- | --- | ---: | ---: |",
    ]
    output.extend(
        f"| {module.lines} | `{module.module}` | `{module.path}` | "
        f"{len(module.public_symbols)} | {len(module.internal_imports)} |"
        for module in largest_modules
    )
    output.extend(
        [
            "",
            "## Largest functions",
            "",
            "| Lines | Complexity | Function | Location |",
            "| ---: | ---: | --- | --- |",
        ]
    )
    output.extend(
        f"| {function.lines} | {function.complexity} | `{function.qualified_name}` | "
        f"`{function.module}:{function.line}` |"
        for function in largest_functions
    )
    output.extend(
        [
            "",
            "## Most complex functions",
            "",
            "| Complexity | Lines | Function | Location |",
            "| ---: | ---: | --- | --- |",
        ]
    )
    output.extend(
        f"| {function.complexity} | {function.lines} | `{function.qualified_name}` | "
        f"`{function.module}:{function.line}` |"
        for function in most_complex
    )
    return "\n".join(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("app"))
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--limit", type=int, default=25)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    inventory = build_inventory(arguments.source_root)
    if arguments.format == "json":
        print(json.dumps(asdict(inventory), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(inventory, limit=arguments.limit))


if __name__ == "__main__":
    main()
