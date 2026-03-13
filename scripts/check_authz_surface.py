#!/usr/bin/env python
from __future__ import annotations

import ast
from pathlib import Path
import sys

MAIN = Path("src/sts_monitor/main.py")
MUTATING = {"post", "patch", "delete", "put"}
# Endpoints intentionally exempt from auth (login, public health, etc.)
AUTH_EXEMPT_PATHS = {"/auth/login", "/health", "/system/preflight"}


def _decorator_route(decorator: ast.AST) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    fn = decorator.func
    if not isinstance(fn, ast.Attribute):
        return None
    if not isinstance(fn.value, ast.Name) or fn.value.id != "app":
        return None
    method = fn.attr
    if method not in {"get", "post", "patch", "delete", "put"}:
        return None
    if not decorator.args:
        return None
    first = decorator.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return method, first.value


def _fn_has_guard(fn_src: str, guard: str) -> bool:
    return guard in fn_src


def main() -> int:
    source = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(source)

    violations: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        routes = [_decorator_route(d) for d in node.decorator_list]
        routes = [r for r in routes if r is not None]
        if not routes:
            continue

        fn_src = ast.get_source_segment(source, node) or ""
        for method, path in routes:
            if path in AUTH_EXEMPT_PATHS:
                continue
            if method in MUTATING:
                if not any(_fn_has_guard(fn_src, token) for token in ("Depends(require_api_key)", "Depends(require_analyst)", "Depends(require_admin)")):
                    violations.append(f"{method.upper()} {path} missing auth dependency")
            if path.startswith("/admin") or path.startswith("/audit"):
                if not _fn_has_guard(fn_src, "Depends(require_admin)"):
                    violations.append(f"{method.upper()} {path} must require_admin")

    if violations:
        print("Authorization surface check failed:", file=sys.stderr)
        for item in violations:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Authorization surface check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
