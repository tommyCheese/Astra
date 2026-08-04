"""Enforce Astra backend dependency, complexity, size, and typing boundaries."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from analyze_backend_architecture import ArchitectureInventory, build_inventory


@dataclass(frozen=True)
class QualityBudget:
    module_lines: int
    function_lines: int
    function_complexity: int


@dataclass(frozen=True)
class ForbiddenDependency:
    importer: str
    imported: str


@dataclass(frozen=True)
class ArchitectureRules:
    default_budget: QualityBudget
    hard_limit: QualityBudget
    forbidden_dependencies: tuple[ForbiddenDependency, ...]
    typed_module_prefixes: tuple[str, ...]
    forbidden_generic_module_names: tuple[str, ...]
    forbidden_top_level_packages: tuple[str, ...]


@dataclass(frozen=True)
class QualityException:
    symbol: str
    owner: str
    reason: str
    expires: date


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_rules(path: Path) -> ArchitectureRules:
    raw_rules = load_json(path)
    return ArchitectureRules(
        default_budget=QualityBudget(**raw_rules["default_budget"]),
        hard_limit=QualityBudget(**raw_rules["hard_limit"]),
        forbidden_dependencies=tuple(
            ForbiddenDependency(**dependency)
            for dependency in raw_rules["forbidden_dependencies"]
        ),
        typed_module_prefixes=tuple(raw_rules["typed_module_prefixes"]),
        forbidden_generic_module_names=tuple(raw_rules["forbidden_generic_module_names"]),
        forbidden_top_level_packages=tuple(raw_rules["forbidden_top_level_packages"]),
    )


def load_exceptions(path: Path) -> dict[str, QualityException]:
    raw_exceptions = load_json(path)
    exceptions: dict[str, QualityException] = {}
    for raw_exception in raw_exceptions.get("exceptions", []):
        exception = QualityException(
            symbol=raw_exception["symbol"],
            owner=raw_exception["owner"],
            reason=raw_exception["reason"],
            expires=date.fromisoformat(raw_exception["expires"]),
        )
        exceptions[exception.symbol] = exception
    return exceptions


def canonical_import_target(imported: str, module_names: set[str]) -> str | None:
    candidate = imported
    while candidate:
        if candidate in module_names:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return None


def dependency_edges(inventory: ArchitectureInventory) -> set[tuple[str, str]]:
    module_names = {module.module for module in inventory.modules}
    edges: set[tuple[str, str]] = set()
    for module in inventory.modules:
        for imported in module.internal_imports:
            target = canonical_import_target(imported, module_names)
            if target and target != module.module:
                edges.add((module.module, target))
    return edges


def matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def forbidden_edges(
    edges: set[tuple[str, str]], rules: ArchitectureRules
) -> set[tuple[str, str]]:
    return {
        (importer, imported)
        for importer, imported in edges
        if any(
            matches_prefix(importer, dependency.importer)
            and matches_prefix(imported, dependency.imported)
            for dependency in rules.forbidden_dependencies
        )
    }


def transitive_reachability(
    nodes: set[str], edges: set[tuple[str, str]]
) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for importer, imported in edges:
        adjacency[importer].add(imported)
    reachable: dict[str, set[str]] = {}
    for start in nodes:
        visited: set[str] = set()
        pending = list(adjacency[start])
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency[current] - visited)
        reachable[start] = visited
    return reachable


def cyclic_pairs(inventory: ArchitectureInventory) -> set[tuple[str, str]]:
    nodes = {module.module for module in inventory.modules}
    reachable = transitive_reachability(nodes, dependency_edges(inventory))
    return {
        (left, right)
        for left in nodes
        for right in reachable[left]
        if left < right and left in reachable.get(right, set())
    }


def type_ignore_counts(source_root: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    resolved_root = source_root.resolve()
    for source_file in resolved_root.rglob("*.py"):
        source_text = source_file.read_text(encoding="utf-8")
        count = source_text.count("type: ignore")
        if count:
            relative = source_file.relative_to(resolved_root.parent).with_suffix("")
            counts[".".join(relative.parts)] = count
    return dict(sorted(counts.items()))


def quality_baseline(
    inventory: ArchitectureInventory,
    rules: ArchitectureRules,
    source_root: Path,
) -> dict[str, Any]:
    default = rules.default_budget
    legacy_modules = {
        module.module: module.lines
        for module in inventory.modules
        if module.lines > default.module_lines
    }
    legacy_functions = {
        f"{function.module}:{function.qualified_name}": {
            "lines": function.lines,
            "complexity": function.complexity,
        }
        for function in inventory.functions
        if function.lines > default.function_lines
        or function.complexity > default.function_complexity
    }
    edges = forbidden_edges(dependency_edges(inventory), rules)
    return {
        "legacy_modules": dict(sorted(legacy_modules.items())),
        "legacy_functions": dict(sorted(legacy_functions.items())),
        "forbidden_edges": [list(edge) for edge in sorted(edges)],
        "cyclic_pairs": [list(pair) for pair in sorted(cyclic_pairs(inventory))],
        "type_ignore_counts": type_ignore_counts(source_root),
    }


def exception_is_valid(exception: QualityException | None) -> bool:
    return bool(
        exception
        and exception.owner.strip()
        and exception.reason.strip()
        and exception.expires >= date.today()
    )


def check_module_budgets(
    inventory: ArchitectureInventory,
    rules: ArchitectureRules,
    baseline: dict[str, Any],
    exceptions: dict[str, QualityException],
) -> Iterable[str]:
    legacy_modules = baseline["legacy_modules"]
    for module in inventory.modules:
        if module.lines <= rules.default_budget.module_lines:
            continue
        symbol = module.module
        if module.lines > rules.hard_limit.module_lines:
            yield f"{symbol} has {module.lines} lines; hard limit is {rules.hard_limit.module_lines}"
            continue
        legacy_lines = legacy_modules.get(symbol)
        if legacy_lines is not None:
            if module.lines > legacy_lines and not exception_is_valid(exceptions.get(symbol)):
                yield f"{symbol} grew from {legacy_lines} to {module.lines} lines"
            continue
        if not exception_is_valid(exceptions.get(symbol)):
            yield f"{symbol} exceeds the default module budget without a valid exception"


def check_function_budgets(
    inventory: ArchitectureInventory,
    rules: ArchitectureRules,
    baseline: dict[str, Any],
    exceptions: dict[str, QualityException],
) -> Iterable[str]:
    legacy_functions = baseline["legacy_functions"]
    for function in inventory.functions:
        exceeds_default = (
            function.lines > rules.default_budget.function_lines
            or function.complexity > rules.default_budget.function_complexity
        )
        if not exceeds_default:
            continue
        symbol = f"{function.module}:{function.qualified_name}"
        if function.lines > rules.hard_limit.function_lines:
            yield (
                f"{symbol} has {function.lines} lines; "
                f"hard limit is {rules.hard_limit.function_lines}"
            )
            continue
        if function.complexity > rules.hard_limit.function_complexity:
            yield (
                f"{symbol} has complexity {function.complexity}; "
                f"hard limit is {rules.hard_limit.function_complexity}"
            )
            continue
        legacy = legacy_functions.get(symbol)
        if legacy is not None:
            if function.lines > legacy["lines"]:
                yield f"{symbol} grew from {legacy['lines']} to {function.lines} lines"
            if function.complexity > legacy["complexity"]:
                yield (
                    f"{symbol} complexity grew from {legacy['complexity']} "
                    f"to {function.complexity}"
                )
            continue
        if not exception_is_valid(exceptions.get(symbol)):
            yield f"{symbol} exceeds the default function budget without a valid exception"


def iter_public_callables(module_tree: ast.Module) -> Iterable[ast.FunctionDef | ast.AsyncFunctionDef]:
    for statement in module_tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not statement.name.startswith("_"):
                yield statement
        elif isinstance(statement, ast.ClassDef) and not statement.name.startswith("_"):
            for member in statement.body:
                if (
                    isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not member.name.startswith("_")
                ):
                    yield member


def check_typed_boundaries(source_root: Path, rules: ArchitectureRules) -> Iterable[str]:
    resolved_root = source_root.resolve()
    for source_file in resolved_root.rglob("*.py"):
        module = ".".join(source_file.relative_to(resolved_root.parent).with_suffix("").parts)
        if not any(matches_prefix(module, prefix) for prefix in rules.typed_module_prefixes):
            continue
        module_tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for function in iter_public_callables(module_tree):
            missing_parameters = [
                argument.arg
                for argument in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
                if argument.arg not in {"self", "cls"} and argument.annotation is None
            ]
            if function.args.vararg and function.args.vararg.annotation is None:
                missing_parameters.append(f"*{function.args.vararg.arg}")
            if function.args.kwarg and function.args.kwarg.annotation is None:
                missing_parameters.append(f"**{function.args.kwarg.arg}")
            location = f"{module}:{function.name}:{function.lineno}"
            if missing_parameters:
                yield f"{location} has untyped parameters: {', '.join(missing_parameters)}"
            if function.returns is None:
                yield f"{location} has no return annotation"


def check_role_package_names(
    inventory: ArchitectureInventory,
    rules: ArchitectureRules,
) -> Iterable[str]:
    for module in inventory.modules:
        parts = module.module.split(".")
        if parts[-1] in rules.forbidden_generic_module_names:
            yield f"{module.module} uses a generic module name"
        if len(parts) > 1 and parts[1] in rules.forbidden_top_level_packages:
            yield f"{module.module} creates a global technical package"


def check_architecture(
    inventory: ArchitectureInventory,
    rules: ArchitectureRules,
    baseline: dict[str, Any],
    exceptions: dict[str, QualityException],
    source_root: Path,
) -> list[str]:
    failures = [
        *check_module_budgets(inventory, rules, baseline, exceptions),
        *check_function_budgets(inventory, rules, baseline, exceptions),
        *check_typed_boundaries(source_root, rules),
        *check_role_package_names(inventory, rules),
    ]
    current_forbidden = forbidden_edges(dependency_edges(inventory), rules)
    baseline_forbidden = {tuple(edge) for edge in baseline["forbidden_edges"]}
    for edge in sorted(current_forbidden - baseline_forbidden):
        failures.append(f"new forbidden dependency: {edge[0]} -> {edge[1]}")
    current_cyclic_pairs = cyclic_pairs(inventory)
    baseline_cyclic_pairs = {tuple(pair) for pair in baseline["cyclic_pairs"]}
    for left, right in sorted(current_cyclic_pairs - baseline_cyclic_pairs):
        failures.append(f"new dependency cycle connects {left} and {right}")
    current_ignores = type_ignore_counts(source_root)
    for module, count in current_ignores.items():
        allowed = baseline["type_ignore_counts"].get(module, 0)
        if count > allowed:
            failures.append(f"{module} adds type: ignore suppressions ({allowed} -> {count})")
    return sorted(failures)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("app"))
    parser.add_argument("--rules", type=Path, default=Path("architecture-rules.json"))
    parser.add_argument("--baseline", type=Path, default=Path("architecture-baseline.json"))
    parser.add_argument("--exceptions", type=Path, default=Path("architecture-exceptions.json"))
    parser.add_argument("--print-baseline", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    rules = load_rules(arguments.rules)
    inventory = build_inventory(arguments.source_root)
    if arguments.print_baseline:
        print(
            json.dumps(
                quality_baseline(inventory, rules, arguments.source_root),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    baseline = load_json(arguments.baseline)
    exceptions = load_exceptions(arguments.exceptions)
    failures = check_architecture(
        inventory,
        rules,
        baseline,
        exceptions,
        arguments.source_root,
    )
    if failures:
        print("Backend architecture check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("Backend architecture check passed")


if __name__ == "__main__":
    main()
