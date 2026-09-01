import ast
import pathlib

import pytest

DASHBOARD_ROOT = pathlib.Path(__file__).resolve().parents[1] / "dashboard"
REPOSITORY_ROOT = DASHBOARD_ROOT.parent

FORBIDDEN_PREFIXES = ("app", "motor", "pymongo", "fastapi", "bson")
ALLOWED_DEPENDENCIES = {"streamlit", "requests", "pandas"}


def dashboard_modules() -> list[pathlib.Path]:
    return sorted(DASHBOARD_ROOT.rglob("*.py"))


def module_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    return imported


def test_the_dashboard_package_exists():
    assert dashboard_modules(), "no dashboard modules were found"


@pytest.mark.parametrize("path", dashboard_modules(), ids=lambda path: path.name)
def test_dashboard_never_imports_the_backend(path):
    offenders = sorted(module_imports(path) & set(FORBIDDEN_PREFIXES))

    assert offenders == [], (
        f"{path.relative_to(REPOSITORY_ROOT)} imports {offenders}. "
        "The dashboard is a presentation layer and may only reach the system "
        "through the FastAPI contract."
    )


def test_dashboard_declares_only_its_own_dependencies():
    requirements = (DASHBOARD_ROOT / "requirements.txt").read_text(encoding="utf-8")
    declared = {
        line.strip().split("==")[0].split(">=")[0].lower()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert declared == ALLOWED_DEPENDENCIES


def test_dashboard_has_no_database_driver_dependency():
    requirements = (DASHBOARD_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

    assert "motor" not in requirements
    assert "pymongo" not in requirements
    assert "fastapi" not in requirements


def test_no_hardcoded_localhost_in_the_dashboard():
    for path in dashboard_modules():
        source = path.read_text(encoding="utf-8")
        assert "localhost" not in source, f"{path.name} hardcodes localhost"
        assert "127.0.0.1" not in source, f"{path.name} hardcodes an IP address"


def test_no_secrets_are_committed():
    assert not (DASHBOARD_ROOT / ".streamlit" / "secrets.toml").exists()
    assert (DASHBOARD_ROOT / ".streamlit" / "secrets.toml.example").exists()


def test_no_npm_artefacts_exist_anywhere():
    for name in ("package.json", "package-lock.json", "node_modules"):
        matches = [
            path
            for path in REPOSITORY_ROOT.rglob(name)
            if ".venv" not in path.parts
        ]
        assert matches == [], f"unexpected npm artefact: {matches}"
