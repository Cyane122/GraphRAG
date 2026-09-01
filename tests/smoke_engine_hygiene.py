# ================================
# tests/smoke_engine_hygiene.py
#
# Engine source hygiene smoke checks: no unused imports and every header-declared
# Class/Function actually exists in the module, across engine-scope src/ packages.
# Stands in for a linter per the F5 decision in
# .re0/iteration/0.1.1-engine-dedup/DESIGN.local.md (tests/ smoke check instead of
# introducing ruff/pyproject.toml).
#
# Functions
#   - _is_header_delimiter(line: str) -> bool : Detect a `# ====...====` header delimiter line.
#   - _header_block_lines(lines: list[str]) -> list[str] | None : Extract the comment lines between the header delimiters.
#   - _section_name(line: str) -> str | None : Detect a bare `# <Name>` header subsection label line.
#   - _bullet_name(line: str) -> str | None : Extract the leading identifier from a header bullet line.
#   - _header_declared_names(lines: list[str]) -> list[tuple[str, str, int]] : Collect (section, name, line number) for every Classes/Functions header bullet.
#   - _import_bindings(tree: ast.Module) -> list[tuple[str, int]] : Collect (bound name, line number) for every import, excluding `from __future__ import annotations` and star imports.
#   - _string_annotation_names(annotation: ast.expr | None) -> set[str] : Parse a quoted forward-reference annotation and return the Name ids inside it.
#   - _used_names(tree: ast.Module) -> set[str] : Collect every referenced Name identifier (including inside quoted annotations) plus module-level `__all__` string entries.
#   - _assign_target_names(target: ast.expr) -> list[str] : Return every plain Name bound by one assignment target, recursing through tuple/list unpacking.
#   - _module_level_bindings(tree: ast.Module) -> set[str] : Collect every name bound in the module's top-level namespace by definition, import, or assignment.
#   - _unused_import_entries(path: Path) -> list[tuple[int, str]] : Return (line, name) for every unused import in `path`.
#   - _find_unused_imports(path: Path, display_path: Path | None = None) -> list[str] : Return one message per import in `path` that is never referenced.
#   - _unused_import_key(display_path: Path, lineno: int, name: str) -> str : Return the compact `path:line:name` baseline key.
#   - _is_module_identity_swap(tree: ast.Module) -> bool : Detect the `sys.modules[__name__] = other_module` compatibility-facade idiom.
#   - _find_undeclared_header_names(path: Path, display_path: Path | None = None) -> list[str] : Return one message per header-declared name in `path` that is not defined or imported.
#   - _iter_python_files(repo_root: Path) -> list[Path] : Return sorted engine-scope .py files.
#   - _current_unused_import_keys(repo_root: Path) -> set[str] : Return the compact key for every unused import currently in scope.
#   - _check_unused_imports(repo_root: Path) -> None : Ratchet-check the current unused-import set against UNUSED_IMPORT_BASELINE.
#   - _check_header_declarations(repo_root: Path) -> None : Validate every header-declared name in engine-scope modules actually exists (no baseline; must stay zero).
#   - run_engine_hygiene_suite(repo_root: Path) -> None : Run the full engine hygiene smoke suite.
#   - main() -> None : Run the standalone engine hygiene smoke suite.
# ================================

from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.engine_hygiene_baseline import UNUSED_IMPORT_BASELINE  # noqa: E402

# Engine-scope packages this suite covers. src/assets/worlds/ is world content and
# excluded per this cycle's non-goals; src/apps/world_editor/ is included because it
# is maintained engine tooling with its own header-block convention and smoke
# coverage (tests/smoke_world_editor_migrate.py, tests/smoke_world_editor_repair.py),
# not authored world/scenario content.
_ENGINE_SCOPE_DIRS = (
    "src/core",
    "src/wiki",
    "src/apps/app",
    "src/simulation",
    "src/apps/world_editor",
)


def _is_header_delimiter(line: str) -> bool:
    """Return True if `line` is a `# ====...====` file-header delimiter line."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    rest = stripped[1:].strip()
    return len(rest) >= 4 and set(rest) == {"="}


def _header_block_lines(lines: list[str]) -> list[str] | None:
    """Return the comment lines strictly between the opening and closing header
    delimiters when `lines[0]` is a delimiter, else None (file has no header block)."""
    if not lines or not _is_header_delimiter(lines[0]):
        return None
    for index in range(1, len(lines)):
        if _is_header_delimiter(lines[index]):
            return lines[1:index]
    return None


def _section_name(line: str) -> str | None:
    """Return the subsection label (e.g. "Classes", "Functions") if `line` is a bare
    `# <Name>` label line inside a header block, else None."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    rest = stripped[1:].strip()
    if not rest or " " in rest or "-" in rest or ":" in rest:
        return None
    return rest


def _bullet_name(line: str) -> str | None:
    """Return the leading identifier of a `#   - name(...) -> Ret : desc` header
    bullet line, or None if `line` is not a bullet line (e.g. a continuation)."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    body = stripped[1:].strip()
    if not body.startswith("- "):
        return None
    body = body[2:]
    name_chars: list[str] = []
    for character in body:
        if character.isalnum() or character == "_":
            name_chars.append(character)
        else:
            break
    name = "".join(name_chars)
    if not name or not (name[0].isalpha() or name[0] == "_"):
        return None
    return name


def _header_declared_names(lines: list[str]) -> list[tuple[str, str, int]]:
    """Collect (section, name, 1-indexed file line number) for every Classes/Functions
    bullet inside the file's header block. Returns an empty list when the file has no
    header block, or when the header declares neither section."""
    block = _header_block_lines(lines)
    if block is None:
        return []
    declarations: list[tuple[str, str, int]] = []
    section: str | None = None
    header_start_line = 2  # line 1 is the opening delimiter; the block starts at line 2
    for offset, line in enumerate(block):
        section_label = _section_name(line)
        if section_label is not None:
            section = section_label
            continue
        if section not in ("Classes", "Functions"):
            continue
        name = _bullet_name(line)
        if name is not None:
            declarations.append((section, name, header_start_line + offset))
    return declarations


def _import_bindings(tree: ast.Module) -> list[tuple[str, int]]:
    """Collect (bound name, line number) for every `import`/`from ... import` alias in
    `tree`, anywhere in the module. Excludes `from __future__ import annotations`
    (always statically "unused") and `import *` (nothing to bind a single name to)."""
    bindings: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                bindings.append((bound, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                bindings.append((bound, node.lineno))
    return bindings


def _string_annotation_names(annotation: ast.expr | None) -> set[str]:
    """If `annotation` is a quoted forward-reference (a string literal, e.g. the
    `"FastAPI"` in `def create_app() -> "FastAPI":` under a `TYPE_CHECKING`-guarded
    import), parse its text as an expression and return every Name id inside — e.g.
    `"list[FastAPI]"` yields `{"list", "FastAPI"}`. Returns an empty set for a
    non-string or unparseable annotation."""
    if not isinstance(annotation, ast.Constant) or not isinstance(annotation.value, str):
        return set()
    try:
        parsed = ast.parse(annotation.value, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(parsed) if isinstance(node, ast.Name)}


def _used_names(tree: ast.Module) -> set[str]:
    """Collect every Name identifier referenced anywhere in `tree` (covers Attribute
    roots, since `a.b.c` is built on a Name node for `a`), every Name inside a quoted
    forward-reference annotation (parameter, return, or variable), plus every string
    element of a module-level `__all__` list/tuple/set assignment (re-export usage, as
    in an `__init__.py` that imports names solely to expose them)."""
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            used.update(_string_annotation_names(node.returns))
            annotated_args = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if node.args.vararg is not None:
                annotated_args.append(node.args.vararg)
            if node.args.kwarg is not None:
                annotated_args.append(node.args.kwarg)
            for arg in annotated_args:
                used.update(_string_annotation_names(arg.annotation))
        elif isinstance(node, ast.AnnAssign):
            used.update(_string_annotation_names(node.annotation))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AugAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    used.add(element.value)
    return used


def _assign_target_names(target: ast.expr) -> list[str]:
    """Return every plain Name bound by one assignment target, recursing through
    tuple/list unpacking (`a, b = ...`) so each unpacked name is counted."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_assign_target_names(element))
        return names
    return []


def _module_level_bindings(tree: ast.Module) -> set[str]:
    """Collect every name bound directly in the module's top-level namespace: class and
    function definitions, imports, and plain/annotated assignments (`NAME = ...` and
    `NAME: T = ...`, including `NAME = other_module.other_name` re-export aliases).
    Recurses into `if`/`try` blocks (but not into function or class bodies) so
    TYPE_CHECKING-guarded or fallback imports still count, matching what
    `from module import name` could actually resolve at runtime."""
    bindings: set[str] = set()

    def visit(statements: list[ast.stmt]) -> None:
        """Walk one list of statements, recursing into module-scope-preserving blocks."""
        for statement in statements:
            if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                bindings.add(statement.name)
            elif isinstance(statement, ast.Import):
                for alias in statement.names:
                    bindings.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name != "*":
                        bindings.add(alias.asname or alias.name)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    bindings.update(_assign_target_names(target))
            elif isinstance(statement, ast.AnnAssign):
                bindings.update(_assign_target_names(statement.target))
            elif isinstance(statement, ast.If):
                visit(statement.body)
                visit(statement.orelse)
            elif isinstance(statement, ast.Try):
                visit(statement.body)
                for handler in statement.handlers:
                    visit(handler.body)
                visit(statement.orelse)
                visit(statement.finalbody)

    visit(tree.body)
    return bindings


def _unused_import_entries(path: Path) -> list[tuple[int, str]]:
    """Return (line number, name) for every import binding in `path` that is never
    referenced by a Name node or a module-level `__all__` export. This is the
    structured form both `_find_unused_imports`'s human-readable message and
    `_unused_import_key`'s compact baseline key are built from."""
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    used = _used_names(tree)
    return [(lineno, name) for name, lineno in _import_bindings(tree) if name not in used]


def _find_unused_imports(path: Path, display_path: Path | None = None) -> list[str]:
    """Return one `"path:line: unused import 'name'"` message per import binding in
    `path` that is never referenced. `display_path` (defaulting to `path`) controls
    what appears before `:line:` — pass a repo-root-relative path to keep messages
    portable across machines/checkouts. Empty list means the file is clean."""
    shown = display_path if display_path is not None else path
    return [
        f"{shown.as_posix()}:{lineno}: unused import '{name}'"
        for lineno, name in _unused_import_entries(path)
    ]


def _unused_import_key(display_path: Path, lineno: int, name: str) -> str:
    """Return the compact `"path:line:name"` identity used for baseline comparison —
    deliberately shorter than `_find_unused_imports`'s human-readable message so
    tests/engine_hygiene_baseline.py reads as a plain, greppable, prunable list."""
    return f"{display_path.as_posix()}:{lineno}:{name}"


def _is_module_identity_swap(tree: ast.Module) -> bool:
    """Return True if the module replaces itself wholesale with `sys.modules[__name__]
    = other_module` (the compatibility-facade idiom used by e.g.
    src/apps/world_editor/source_create.py). Such a module's real public surface is
    whatever `other_module` defines, which is not visible to static top-level-binding
    analysis, so header verification cannot be done here and must be skipped."""
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if not isinstance(target, ast.Subscript):
                continue
            value = target.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "modules"
                and isinstance(value.value, ast.Name)
                and value.value.id == "sys"
                and isinstance(target.slice, ast.Name)
                and target.slice.id == "__name__"
            ):
                return True
    return False


def _find_undeclared_header_names(path: Path, display_path: Path | None = None) -> list[str]:
    """Return one message per header Classes/Functions bullet in `path` whose name is
    not bound anywhere in the module's top-level namespace (definition or import). The
    reverse direction (a real top-level def not listed in the header) is intentionally
    not checked: partial header listings are an accepted convention in this repo.
    `display_path` (defaulting to `path`) controls what appears before `:line:`.
    Empty list means the header matches the module (or the file has no header block, or
    the module is a `sys.modules[__name__]` identity-swap facade whose real surface
    lives in another, separately-checked module)."""
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    declared = _header_declared_names(lines)
    if not declared:
        return []
    tree = ast.parse(source, filename=str(path))
    if _is_module_identity_swap(tree):
        return []
    bound = _module_level_bindings(tree)
    shown = display_path if display_path is not None else path
    return [
        f"{shown.as_posix()}:{lineno}: header declares {section} '{name}' but it is "
        "not defined or imported in the module"
        for section, name, lineno in declared
        if name not in bound
    ]


def _iter_python_files(repo_root: Path) -> list[Path]:
    """Return sorted `.py` files under the engine-scope directories in `repo_root`,
    excluding `__pycache__`. Scope directories that do not exist are skipped."""
    files: list[Path] = []
    for relative in _ENGINE_SCOPE_DIRS:
        scope_root = repo_root / relative
        if not scope_root.is_dir():
            continue
        files.extend(
            path for path in scope_root.rglob("*.py") if "__pycache__" not in path.parts
        )
    return sorted(files)


def _current_unused_import_keys(repo_root: Path) -> set[str]:
    """Return the compact `"path:line:name"` key for every unused import currently
    present across engine-scope modules under `repo_root`."""
    current: set[str] = set()
    for path in _iter_python_files(repo_root):
        display_path = path.relative_to(repo_root)
        for lineno, name in _unused_import_entries(path):
            current.add(_unused_import_key(display_path, lineno, name))
    return current


def _check_unused_imports(repo_root: Path) -> None:
    """Ratchet check: validate that the current set of unused-import violations across
    engine-scope modules under `repo_root` is exactly the frozen
    UNUSED_IMPORT_BASELINE (tests/engine_hygiene_baseline.py) — no more, no less.

    This is deliberately not a plain "assert zero" check: 84 pre-existing violations
    predate this suite (see F5 in .re0/iteration/0.1.1-engine-dedup/DESIGN.local.md)
    and fixing product code is out of this cycle's scope. The baseline freezes that
    known debt so the suite can still be green, while catching two things a bare
    baseline file could not on its own: a *new* violation anywhere in scope (fails
    immediately, so the bug class this suite exists to catch cannot grow back), and a
    baseline entry that has already been fixed elsewhere but was left listed (fails
    too, so the baseline is forced to shrink instead of silently going stale)."""
    current = _current_unused_import_keys(repo_root)
    baseline = set(UNUSED_IMPORT_BASELINE)
    new_violations = sorted(current - baseline)
    fixed_violations = sorted(baseline - current)
    assert not new_violations, (
        "새로 생긴 위반 — tests/engine_hygiene_baseline.py의 UNUSED_IMPORT_BASELINE에 "
        "없는 새 미사용 import입니다. 진짜 위반이면 코드를 고치고, 이 사이클 범위 밖의 "
        "기존 부채라면 baseline에 추가하되 그 판단을 별도로 검토받으세요:\n"
        + "\n".join(new_violations)
    )
    assert not fixed_violations, (
        "고쳐진 항목이 baseline에 남아 있습니다 — "
        "tests/engine_hygiene_baseline.py의 UNUSED_IMPORT_BASELINE에서 지우세요 "
        "(이 목록은 줄어들기만 해야 합니다):\n"
        + "\n".join(fixed_violations)
    )


def _check_header_declarations(repo_root: Path) -> None:
    """Validate that every header-declared Class/Function in engine-scope modules
    under `repo_root` is actually defined or imported in that module. Unlike
    `_check_unused_imports`, there is no baseline here: this check was already at zero
    violations when introduced, so it stays a plain "assert zero" gate — a baseline
    would only exist to hide debt that does not exist yet."""
    violations: list[str] = []
    for path in _iter_python_files(repo_root):
        violations.extend(_find_undeclared_header_names(path, path.relative_to(repo_root)))
    assert not violations, "header/body mismatches found:\n" + "\n".join(violations)


def run_engine_hygiene_suite(repo_root: Path) -> None:
    """Run the full engine source-hygiene smoke suite against `repo_root`."""
    _check_unused_imports(repo_root)
    _check_header_declarations(repo_root)


def main() -> None:
    """Run the standalone engine hygiene smoke suite."""
    run_engine_hygiene_suite(ROOT)
    print("smoke_engine_hygiene: ok")


if __name__ == "__main__":
    main()
