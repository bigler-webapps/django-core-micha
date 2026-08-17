"""Keep runtime imports aligned with the package's declared dependencies."""

import ast
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "django_core_micha"

# This deliberately remains explicit: import names and distribution names do not
# always match, and Django brings asgiref as an unconditional dependency.
MODULE_TO_DISTRIBUTION = {
    "PIL": "Pillow",
    "allauth": "django-allauth",
    "anthropic": "anthropic",
    "asgiref": "Django",
    "channels": "channels",
    "corsheaders": "django-cors-headers",
    "cryptography": "cryptography",
    "django": "Django",
    "environ": "django-environ",
    "filetype": "filetype",
    "pywebpush": "pywebpush",
    "pypdf": "pypdf",
    "openai": "openai",
    "rest_framework": "djangorestframework",
    "yaml": "PyYAML",
}


def _declared_distribution_names() -> set[str]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        dependencies = tomllib.load(pyproject_file)["project"]["dependencies"]

    return {
        dependency.split("[", 1)[0].split(";", 1)[0]
        .split("=", 1)[0]
        .split("<", 1)[0]
        .split(">", 1)[0]
        .split("!", 1)[0]
        .split("~", 1)[0]
        .strip()
        .lower()
        for dependency in dependencies
    }


def _runtime_python_files() -> list[Path]:
    return [path for path in SOURCE_ROOT.rglob("*.py") if "tests" not in path.parts]


def _third_party_imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = (node.module,)
        else:
            continue

        for name in names:
            top_level = name.split(".", 1)[0]
            if (
                top_level not in sys.stdlib_module_names
                and top_level != "django_core_micha"
            ):
                imports.append((top_level, node.lineno))

    return imports


def test_runtime_imports_are_declared_dependencies():
    declared = _declared_distribution_names()
    missing = []

    for path in _runtime_python_files():
        for module, line_number in _third_party_imports(path):
            distribution = MODULE_TO_DISTRIBUTION.get(module)
            if distribution is None or distribution.lower() not in declared:
                relative_path = path.relative_to(PROJECT_ROOT)
                missing.append(
                    f"{relative_path}:{line_number} imports {module!r} "
                    f"(expected distribution {distribution!r})"
                )

    assert not missing, "Undeclared third-party runtime imports:\n" + "\n".join(missing)
