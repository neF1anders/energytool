"""Плоская сборка ZIP для этапа 1 (spec §13): `uv run python tools/build_submission.py`."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = "energytool"
ENTRY = ROOT / "solution.py"
ALLOWED_REQUIREMENTS = ROOT / "materials" / "data" / "requirements.txt"
DEFAULT_OUTPUT = ROOT / "dist" / "submission.zip"
MAX_ZIP_BYTES = 128 * 1024 * 1024

_IMPORT = re.compile(rf"^(\s*from\s+){PACKAGE}((?:\.\w+)+)(\s+import\b)", re.MULTILINE)
_PLAIN_IMPORT = re.compile(rf"^(\s*import\s+){PACKAGE}((?:\.\w+)+)", re.MULTILINE)


class SubmissionBuildError(RuntimeError):
    """Сборка невозможна: неразрешённая зависимость, относительный импорт, размер."""


def module_file(dotted: str) -> Path | None:
    path = SRC.joinpath(*dotted.split("."))
    if path.with_suffix(".py").is_file():
        return path.with_suffix(".py")
    if (path / "__init__.py").is_file():
        return path / "__init__.py"
    return None


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise SubmissionBuildError(f"{path}: относительный импорт запрещён")
            if node.module and node.module.split(".")[0] == PACKAGE:
                modules.add(node.module)
                # from energytool.a import b, где b — подмодуль: после переписывания это
                # атрибут модуля energytool__a, которого нет — ломается только в ZIP
                submodules = [
                    alias.name
                    for alias in node.names
                    if module_file(f"{node.module}.{alias.name}") is not None
                ]
                if submodules:
                    raise SubmissionBuildError(
                        f"{path}: импорт подмодулей {submodules} из {node.module} — "
                        "импортируйте функции и классы из самих модулей"
                    )
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names if a.name.split(".")[0] == PACKAGE)
    return modules


def reachable_modules(entry: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    queue = sorted(imported_modules(entry))
    while queue:
        dotted = queue.pop()
        if dotted in found:
            continue
        path = module_file(dotted)
        if path is None:
            raise SubmissionBuildError(f"модуль {dotted} не найден в {SRC}")
        found[dotted] = path
        queue.extend(sorted(imported_modules(path) - found.keys()))
    return found


def flat_name(dotted: str) -> str:
    return dotted.replace(".", "__")


def rewrite_imports(source: str) -> str:
    source = _IMPORT.sub(
        lambda m: f"{m.group(1)}{flat_name(PACKAGE + m.group(2))}{m.group(3)}", source
    )
    return _PLAIN_IMPORT.sub(lambda m: f"{m.group(1)}{flat_name(PACKAGE + m.group(2))}", source)


def requirements() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps: list[str] = project["project"]["dependencies"]
    for dep in deps:
        if "==" not in dep:
            raise SubmissionBuildError(f"зависимость без точной версии: {dep}")
    if ALLOWED_REQUIREMENTS.is_file():
        allowed = {
            line.strip().lower()
            for line in ALLOWED_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        missing = [d for d in deps if d.lower() not in allowed]
        if missing:
            raise SubmissionBuildError(f"нет в разрешённом списке: {missing}")
    else:
        print(f"! {ALLOWED_REQUIREMENTS} не найден: сверка с разрешённым списком пропущена")
    return sorted(deps)


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_defaults(pairs: list[str]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not _ENV_KEY.match(key):
            raise SubmissionBuildError(f"--env ожидает KEY=VAL, получено {pair!r}")
        defaults[key] = value
    return defaults


def with_defaults(source: str, defaults: dict[str, str]) -> str:
    """Вставляет os.environ.setdefault(...) перед импортом пакета: так вариант конфигурации
    (например, BATCH_PIPELINE=full) уезжает в ZIP без правки кода, а переменные
    платформы по-прежнему важнее."""
    if not defaults:
        return source
    lines = ["import os", *(f"os.environ.setdefault({k!r}, {v!r})" for k, v in defaults.items())]
    marker = "import sys\n"
    return source.replace(marker, marker + "\n".join(lines) + "\n", 1)


def build(output: Path = DEFAULT_OUTPUT, defaults: dict[str, str] | None = None) -> Path:
    modules = reachable_modules(ENTRY)
    files = {
        f"{flat_name(d)}.py": rewrite_imports(p.read_text(encoding="utf-8"))
        for d, p in modules.items()
    }
    files["solution.py"] = with_defaults(
        rewrite_imports(ENTRY.read_text(encoding="utf-8")), defaults or {}
    )
    leftovers = [
        name
        for name, text in files.items()
        if re.search(rf"^\s*(from|import)\s+{PACKAGE}\.", text, re.M)
    ]
    if leftovers:
        raise SubmissionBuildError(f"не переписаны импорты в: {leftovers}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
        archive.writestr("requirements.txt", "\n".join(requirements()) + "\n")
    size = output.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise SubmissionBuildError(f"ZIP {size} байт больше лимита {MAX_ZIP_BYTES}")
    print(f"{output}: {len(files)} .py + requirements.txt, {size / 1024:.0f} КБ")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="умолчание переменной окружения, зашиваемое в solution.py (env платформы важнее)",
    )
    args = parser.parse_args(argv)
    try:
        build(args.output, parse_defaults(args.env))
    except SubmissionBuildError as exc:
        print(f"ошибка сборки: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
