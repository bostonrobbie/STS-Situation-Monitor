#!/usr/bin/env python
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

README = Path("README.md")
MAIN = Path("src/sts_monitor/main.py")
ENDPOINT_LINE = re.compile(r"^- `(?:GET|POST|PATCH|PUT|DELETE) ([^`]+)`", re.M)
PATH_PARAM = re.compile(r"\{[^/{}]+\}")


def normalize_endpoint(path: str) -> str:
    """Normalize route params so {id} and {investigation_id} compare equally."""
    return PATH_PARAM.sub("{}", path)


def collect_readme_endpoints(text: str) -> set[str]:
    return {normalize_endpoint(m.group(1).strip()) for m in ENDPOINT_LINE.finditer(text)}


def collect_code_endpoints(text: str) -> set[str]:
    tree = ast.parse(text)
    endpoints: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            if not isinstance(fn, ast.Attribute):
                continue
            if not isinstance(fn.value, ast.Name) or fn.value.id != "app":
                continue
            if fn.attr not in {"get", "post", "patch", "put", "delete"}:
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                endpoints.add(dec.args[0].value)
    return {normalize_endpoint(endpoint) for endpoint in endpoints}


def main() -> int:
    readme_text = README.read_text(encoding="utf-8")
    main_text = MAIN.read_text(encoding="utf-8")

    readme_eps = collect_readme_endpoints(readme_text)
    code_eps = collect_code_endpoints(main_text)

    missing = sorted(ep for ep in readme_eps if ep not in code_eps)
    if missing:
        print(f"README endpoint list contains missing routes: {missing}", file=sys.stderr)
        return 1

    print(f"README endpoint sync check passed. documented={len(readme_eps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
