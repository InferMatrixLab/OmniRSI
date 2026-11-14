"""First-wave Knowledge compatibility gate helpers (P0b).

Imported by the import-gate and isolation-policy tests. This is a test
support module, not a runtime package.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "src" / "inferrsi"
MANIFEST = REPO / "docs" / "legacy-knowledge-manifest.json"
NEW_CORES = ("knowledge", "benchmark", "apps")
CORE_NAMES = ("knowledge", "harness", "benchmark", "apps")
# Current application seams, until the Apps package migration. These are not
# Knowledge compatibility shells and do not grant access to other flat modules.
APPLICATION_ENTRIES = ("cli.py", "benchmark_operations.py")
# Reviewed execution boundaries: owner function (or module scope) and the exact
# local spec variable. They execute task resources/candidate code, not a hidden
# core compatibility import. Their presence keeps isolation incomplete.
RESOURCE_LOADERS = {
    "inferrsi.harness.task_protocol": ("protocol", "spec"),
    "inferrsi.benchmark.tasks.moe-forward-npu.benchmark": ("<module>", "_process_spec"),
    "inferrsi.benchmark.tasks.moe-forward-npu.grader": ("load", "spec"),
    "inferrsi.benchmark.tasks.qwen21-denoise-npu.benchmark": ("load", "spec"),
    "inferrsi.benchmark.tasks.qwen3-tts-npu.benchmark": ("<module>", "_evaluation_spec"),
}
# New H entries: module paths whose closures count toward the new-core result.
# H01 added ``run_store`` and ``errors``; H02 added the public ``api`` with
# ``execution_service``, ``input_binding`` and ``local_process``.
NEW_H_ENTRIES = (
    "inferrsi.harness.api",
    "inferrsi.harness.errors",
    "inferrsi.harness.execution_service",
    "inferrsi.harness.input_binding",
    "inferrsi.harness.local_process",
    "inferrsi.harness.run_store",
)
# The P0a canonical encoder; the new K closure may use it and the data leaf.
CANONICAL_ENCODER = "inferrsi._json_v1"
# Names that load or build modules at run time. Any use fails closed.
# P0b scans only these three spellings; getattr, exec, eval, runpy, and
# pkgutil are intentionally out of scope here.
DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "import_module", "module_from_spec"})


def _from_base(node: ast.ImportFrom, package: str) -> str:
    """Absolute module named by an ``ImportFrom`` inside ``package``."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    keep = parts[: len(parts) - node.level + 1]
    return ".".join(keep + ([node.module] if node.module else []))


def _modules_in_source(source: str, package_parts: list[str]) -> list[str]:
    """Resolve import targets, including ``from package import name`` forms."""
    package = ".".join(package_parts)
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _from_base(node, package)
            if base:
                names.append(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                names.append(candidate)
    return names


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _core_of(module: str) -> str | None:
    for name in CORE_NAMES:
        if _matches(module, f"inferrsi.{name}"):
            return name
    return None


def _imported_modules(path: Path, root: Path) -> list[str]:
    relative = path.relative_to(root)
    return _modules_in_source(
        path.read_text(encoding="utf-8"),
        ["inferrsi", *relative.parts[:-1]],
    )


def _module_name(relative: Path) -> str:
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("inferrsi", *parts))


def _module_index(root: Path) -> dict[str, Path]:
    return {_module_name(path.relative_to(root)): path for path in sorted(root.rglob("*.py"))}


def _resolve(name: str, modules: dict[str, Path]) -> str:
    """Map ``module.attribute`` to its module; keep submodule names."""
    parent = name.rpartition(".")[0]
    if name not in modules and parent in modules and modules[parent].name != "__init__.py":
        return parent
    return name


def cross_core_violations(
    root: Path = ROOT, allowed: frozenset[tuple[str, str]] = frozenset()
) -> list[str]:
    """One message per forbidden importer and target core.

    Same-core imports, including relative imports rewritten to absolute
    names, are allowed. Apps may import the other cores. Every other import
    of ``inferrsi.knowledge``, ``inferrsi.benchmark``, or ``inferrsi.apps``
    is forbidden, as is Knowledge or Benchmark importing Harness. ``allowed``
    holds exact (importer module, target module) pairs; a prefix never counts.
    """
    modules = _module_index(root)
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        importer = relative.parts[0] if len(relative.parts) > 1 else ""
        if relative.as_posix() in APPLICATION_ENTRIES:
            importer = "apps"
        importer_module = _module_name(relative)
        for module in _imported_modules(path, root):
            target = _core_of(module)
            if target is None or importer == "apps" or importer == target:
                continue
            if target == "harness" and importer not in ("knowledge", "benchmark"):
                continue
            if (importer_module, _resolve(module, modules)) in allowed:
                continue
            found.add((relative.as_posix(), target))
    return [f"{path} imports inferrsi.{target}" for path, target in sorted(found)]


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def exact_pairs(manifest: dict[str, Any]) -> frozenset[tuple[str, str]]:
    """The exact (importer module, target module) compatibility allowlist."""
    return frozenset(
        (f"inferrsi.{item['importer']}", item["target"])
        for item in manifest["compatibility_pairs"]
    )


def shell_sources(manifest: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    """Old root leaf -> {exact target: names that leaf may re-export}."""
    leaf = manifest["technical_leaf"]["module"]
    leaf_names = set(manifest["catalog_two_routes"]["neutral_leaf_export"]["symbols"])
    sources: dict[str, dict[str, set[str]]] = {}
    for item in manifest["compatibility_pairs"]:
        if _core_of(item["target"]) == "knowledge":
            exports = item["exports"]
            names = set(exports["public"]) | set(exports["private_used"])
            sources.setdefault(f"inferrsi.{item['importer']}", {})[item["target"]] = names
    for item in manifest["compatibility_pairs"]:
        importer = f"inferrsi.{item['importer']}"
        if item["target"] == leaf and importer in sources:
            sources[importer][leaf] = leaf_names
    return sources


@dataclass(frozen=True)
class _Graph:
    trees: dict[str, ast.Module]
    packages: dict[str, str]
    edges: dict[str, set[str]]


def _package_ancestors(name: str, modules: dict[str, Path]) -> set[str]:
    """Parent packages whose ``__init__`` Python executes on this import.

    Used both for import targets and for the imported module itself. The
    root ``inferrsi`` package init is excluded; it runs for every import
    and is not a business edge.
    """
    parts = name.split(".")
    found: set[str] = set()
    for end in range(2, len(parts)):
        parent = ".".join(parts[:end])
        if parent in modules:
            found.add(parent)
    return found


def _edges(tree: ast.Module, package: str, modules: dict[str, Path]) -> set[str]:
    """Resolved ``inferrsi.*`` targets of every import in one module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _from_base(node, package)
            for alias in node.names:
                candidate = f"{base}.{alias.name}"
                found.add(candidate if candidate in modules else base)
    resolved = {name for name in found if name.startswith("inferrsi.")}
    ancestors: set[str] = set()
    for name in resolved:
        ancestors.update(_package_ancestors(name, modules))
    return resolved | ancestors


def _scan(root: Path, pairs: frozenset[tuple[str, str]]) -> _Graph:
    """The ``src/inferrsi/**`` transfer graph plus manifest-declared edges."""
    modules = _module_index(root)
    trees = {name: ast.parse(path.read_text(encoding="utf-8")) for name, path in modules.items()}
    packages = {
        name: ".".join(("inferrsi", *path.relative_to(root).parts[:-1]))
        for name, path in modules.items()
    }
    edges = {name: _edges(trees[name], packages[name], modules) for name in modules}
    # Importing a module also executes its own parent package inits, even
    # when the module body has no imports of its own.
    for name in modules:
        edges[name].update(_package_ancestors(name, modules))
    # A declared export edge counts once both ends exist, even if unwritten.
    for importer, target in pairs:
        if importer in modules and target in modules:
            edges[importer].add(target)
    return _Graph(trees, packages, edges)


def _resource_loader(tree: ast.Module, module: str) -> ast.Attribute | None:
    declaration = RESOURCE_LOADERS.get(module)
    if declaration is None:
        return None
    matches = []

    def visit(node, owner="<module>"):
        if isinstance(node, ast.ClassDef):
            owner += ".<class>." + node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name if owner == "<module>" else owner + "." + node.name
        if (isinstance(node, ast.Call) and owner == declaration[0]
                and ast.unparse(node.func) == "importlib.util.module_from_spec"
                and len(node.args) == 1 and not node.keywords
                and isinstance(node.args[0], ast.Name) and node.args[0].id == declaration[1]):
            matches.append(node.func)
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree)
    return matches[0] if len(matches) == 1 else None


def _has_dynamic_import(tree: ast.Module, module: str = "") -> bool:
    declared = _resource_loader(tree, module)
    for node in ast.walk(tree):
        if node is declared:
            continue
        if isinstance(node, ast.Name) and node.id in DYNAMIC_IMPORT_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in DYNAMIC_IMPORT_NAMES:
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name in DYNAMIC_IMPORT_NAMES for alias in node.names
        ):
            return True
    return False


def _walk(
    start: str, edges: dict[str, set[str]], forbidden: Callable[[str], bool]
) -> tuple[list[str], list[tuple[str, str]]]:
    """Breadth-first closure. Forbidden nodes are reported, not expanded."""
    parents: dict[str, str | None] = {start: None}
    queue = [start]
    expanded: list[str] = []
    hits: list[tuple[str, str]] = []
    for node in queue:  # the queue grows while it is read
        if node != start and forbidden(node):
            chain = [node]
            while parents[chain[-1]] is not None:
                chain.append(parents[chain[-1]])
            hits.append((node, " -> ".join(reversed(chain))))
            continue
        expanded.append(node)
        for target in sorted(edges.get(node, ())):
            if target not in parents:
                parents[target] = node
                queue.append(target)
    return expanded, hits


def _closure_violations(
    start: str, graph: _Graph, forbidden: Callable[[str], bool]
) -> list[str]:
    expanded, hits = _walk(start, graph.edges, forbidden)
    found = [f"{start} reaches {node}: {path}" for node, path in hits]
    found += [
        f"{start} reaches an undeclared dynamic import in {node}"
        for node in expanded
        if node in graph.trees and _has_dynamic_import(graph.trees[node], node)
    ]
    return found


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _assigned_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _literal_all(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.value, ast.List)
        and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in node.value.elts
        )
    )


def _shell_errors(
    module: str, tree: ast.Module, package: str, sources: dict[str, set[str]]
) -> list[str]:
    """A01-2: a compatibility shell is docstring, future, listed imports, __all__."""
    errors: list[str] = []
    for index, node in enumerate(tree.body):
        if index == 0 and _is_docstring(node):
            continue
        if isinstance(node, ast.ImportFrom):
            base = _from_base(node, package)
            if base == "__future__":
                continue
            if base not in sources:
                errors.append(f"{module} has an unlisted import from {base}")
                continue
            for alias in node.names:
                if alias.name == "*":
                    errors.append(f"{module} has a star import from {base}")
                elif alias.asname not in (None, alias.name):
                    errors.append(f"{module} aliases {alias.name} as {alias.asname}")
                elif alias.name not in sources[base]:
                    errors.append(f"{module} imports unlisted name {base}.{alias.name}")
        elif isinstance(node, ast.Import):
            names = ", ".join(alias.name for alias in node.names)
            errors.append(f"{module} has an unlisted import of {names}")
        elif "__all__" in _assigned_names(node):
            if not _literal_all(node):
                errors.append(f"{module} __all__ is not a literal list of constant strings")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "__getattr__"
        ):
            errors.append(f"{module} defines __getattr__")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            errors.append(f"{module} has a function or class body {node.name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            errors.append(f"{module} has a module-level assignment")
        else:
            errors.append(f"{module} has a {type(node).__name__} statement")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "modules"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            errors.append(f"{module} touches sys.modules")
            break
    if _has_dynamic_import(tree):
        errors.append(f"{module} has a dynamic import")
    return errors


def _allowed_key(entry: str) -> str:
    """``from M import n`` -> ``M.n``; a bare dotted entry stays as is."""
    if not entry.startswith("from "):
        return entry
    node = ast.parse(entry).body[0]
    return f"{node.module}.{node.names[0].name}"


def _leaf_errors(tree: ast.Module, technical_leaf: dict[str, Any]) -> list[str]:
    """The neutral data leaf: listed stdlib imports anywhere, two symbols only."""
    module = technical_leaf["module"]
    allowed = {_allowed_key(entry) for entry in technical_leaf["allowed_imports"]}
    symbols = set(technical_leaf["two_symbols"])
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = _from_base(node, "inferrsi")
            names = [base if base in allowed else f"{base}.{alias.name}" for alias in node.names]
        else:
            continue
        errors.extend(f"{module} imports {name}" for name in names if name not in allowed)
    if _has_dynamic_import(tree):
        errors.append(f"{module} has a dynamic import")
    defined: set[str] = set()
    for index, node in enumerate(tree.body):
        if (index == 0 and _is_docstring(node)) or isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        else:
            names = _assigned_names(node) or [type(node).__name__]
        for name in names:
            if name in symbols:
                defined.add(name)
            else:
                errors.append(f"{module} defines {name} at top level")
    errors.extend(f"{module} lacks {name}" for name in sorted(symbols - defined))
    return errors


def _model_or_infra(module: str) -> bool:
    return _matches(module, "inferrsi.model") or _matches(module, "inferrsi.infra")


@dataclass(frozen=True)
class IsolationReport:
    """Staged first-wave result (A01-4).

    ``new_core_closure_isolated`` covers only the new-core closures present in
    the tree. It never stands in for ``isolation_complete``.
    """

    violations: tuple[str, ...]
    # Every remaining ``harness`` path into Model or Infra, one per hit.
    harness_model_infra: tuple[str, ...]
    blockers: tuple[str, ...]
    new_core_closure_isolated: bool

    @property
    def isolation_complete(self) -> bool:
        return not self.blockers


def blocker_kinds(report: IsolationReport) -> set[str]:
    return {blocker.split(":", 1)[0] for blocker in report.blockers}


def first_wave_report(
    root: Path = ROOT,
    manifest: dict[str, Any] | None = None,
    h_entries: tuple[str, ...] = NEW_H_ENTRIES,
) -> IsolationReport:
    """Exact first-wave policy over ``root`` plus the staged isolation result."""
    manifest = load_manifest() if manifest is None else manifest
    pairs = exact_pairs(manifest)
    leaf = manifest["technical_leaf"]["module"]
    sources = shell_sources(manifest)
    old_leaves = set(sources)
    graph = _scan(root, pairs)
    modules = sorted(graph.trees)

    violations = cross_core_violations(root, pairs)
    leaf_importers = {importer for importer, target in pairs if target == leaf}
    violations += [
        f"{name} is a second importer of {leaf}"
        for name in modules
        if leaf in graph.edges[name]
        and name not in leaf_importers
        and _core_of(name) != "knowledge"
    ]
    for name in sorted(old_leaves & set(modules)):
        # An old leaf that reaches into K or the data leaf is a shell.
        if any(_core_of(t) == "knowledge" or t == leaf for t in graph.edges[name]):
            violations += _shell_errors(
                name, graph.trees[name], graph.packages[name], sources[name]
            )
    if leaf in graph.trees:
        violations += _leaf_errors(graph.trees[leaf], manifest["technical_leaf"])

    def outside_knowledge(node: str) -> bool:
        return _core_of(node) != "knowledge" and node not in (leaf, CANONICAL_ENCODER)

    def benchmark_forbidden(node: str) -> bool:
        return _core_of(node) in ("knowledge", "harness", "apps") or node in old_leaves

    def h_entry_forbidden(node: str) -> bool:
        # Every harness module, including a new H entry, may not reach a new
        # core, an old root leaf, or the data leaf. No importer is exempt.
        return _core_of(node) in NEW_CORES or node in old_leaves or node == leaf

    rules: dict[str, Callable[[str], bool]] = {
        "knowledge": outside_knowledge,
        "benchmark": benchmark_forbidden,
        # Apps calls core public APIs and never passes through an old root leaf.
        "apps": lambda node: node in old_leaves,
    }
    starts = [(name, rules[_core_of(name)]) for name in modules if _core_of(name) in rules]
    starts += [(name, h_entry_forbidden) for name in h_entries if name in graph.trees]
    new_core = [v for name, rule in starts for v in _closure_violations(name, graph, rule)]
    infra = [
        v
        for name in modules
        if _matches(name, "inferrsi.infra")
        for v in _closure_violations(name, graph, lambda node: _core_of(node) is not None)
    ]

    # Non-entry harness modules follow the same rule on the same full graph.
    # They add violations without counting toward the new-core result.
    declared = set(h_entries)
    harness_extra = [
        message
        for name in modules
        if _core_of(name) == "harness" and name not in declared
        for message in _closure_violations(name, graph, h_entry_forbidden)
    ]
    violations += new_core + infra + harness_extra

    blockers = []
    for name in (*NEW_CORES, "harness"):
        package = root / name
        if not package.is_dir():
            blockers.append(f"absent: {name}/")
        elif not any(package.rglob("*.py")):
            # An empty directory is not a present, scannable package (A01-4).
            blockers.append(f"unscannable: {name}/")
    if pairs:
        blockers.append(f"allowlist: {len(pairs)} compatibility pairs")
    legacy = [
        path
        for name in modules
        if _core_of(name) == "harness"
        for _, path in _walk(name, graph.edges, _model_or_infra)[1]
    ]
    if legacy:
        blockers.append(f"harness reaches model/infra: {legacy[0]} ({len(legacy)} hits)")
    if violations:
        blockers.append(f"violations: {len(violations)}")
    resource_loaders = [name for name in modules
                        if _resource_loader(graph.trees[name], name) is not None]
    if resource_loaders:
        blockers.append(f"runtime resource loaders: {len(resource_loaders)}")
    return IsolationReport(
        tuple(violations), tuple(legacy), tuple(blockers),
        bool(starts) and not new_core and not resource_loaders,
    )

