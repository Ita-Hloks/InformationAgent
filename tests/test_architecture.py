from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "information_agent"
ALLOWED_EXTERNAL_DEPENDENCIES = {
    "common": set(),
    "collection": {"common"},
    "normalization": {"common", "collection"},
    "selection": {"common", "normalization"},
    "storage": {"collection", "normalization", "selection"},
    "investigation": {"common", "selection"},
    "search": {"common", "investigation"},
    "analysis": {"common", "selection"},
    "orchestration": {
        "common",
        "collection",
        "investigation",
        "normalization",
        "selection",
        "search",
        "storage",
        "analysis",
    },
}


def _source_stage(source: Path) -> str | None:
    relative = source.relative_to(PACKAGE_ROOT)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _resolve_import(source: Path, statement: ast.ImportFrom) -> str:
    package_parts = ["information_agent", *source.relative_to(PACKAGE_ROOT).parent.parts]
    if statement.level:
        package_parts = package_parts[: 1 - statement.level]
    module_parts = statement.module.split(".") if statement.module else []
    return ".".join([*package_parts, *module_parts])


def _imported_modules(source: Path) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: list[str] = []
    for statement in ast.walk(tree):
        if isinstance(statement, ast.Import):
            modules.extend(alias.name for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            modules.append(_resolve_import(source, statement))
    return modules


def test_production_modules_use_only_public_upstream_interfaces() -> None:
    violations: list[str] = []
    for source in PACKAGE_ROOT.rglob("*.py"):
        source_stage = _source_stage(source)
        if source_stage not in ALLOWED_EXTERNAL_DEPENDENCIES:
            continue

        for module in _imported_modules(source):
            parts = module.split(".")
            if len(parts) < 2 or parts[0] != "information_agent":
                continue
            target_stage = parts[1]
            if target_stage == source_stage:
                continue
            if target_stage in ALLOWED_EXTERNAL_DEPENDENCIES:
                if target_stage not in ALLOWED_EXTERNAL_DEPENDENCIES[source_stage]:
                    violations.append(f"{source}: forbidden dependency on {module}")
                elif len(parts) > 2:
                    violations.append(f"{source}: bypasses public API with {module}")

    assert not violations, "\n".join(violations)
