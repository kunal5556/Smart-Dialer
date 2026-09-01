import ast
import inspect
import pathlib

import pytest

from app.services.call_allocator import CallAllocator

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

PROVIDER_PACKAGE = "app.providers"
ALLOCATOR_MODULE = "app.services.call_allocator"


def module_imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    return imported


def python_files(package: str) -> list[pathlib.Path]:
    return sorted((APP_ROOT / package).rglob("*.py"))


@pytest.mark.parametrize("package", ["pacing", "safety"])
def test_package_does_not_import_the_telecom_provider(package):
    offenders = []
    for path in python_files(package):
        for imported in module_imports(path):
            if imported == PROVIDER_PACKAGE or imported.startswith(PROVIDER_PACKAGE + "."):
                offenders.append(f"{path.name} imports {imported}")

    assert offenders == [], (
        "Pacing and safety code must never reach the telecom provider directly: "
        + "; ".join(offenders)
    )


@pytest.mark.parametrize("package", ["pacing", "safety"])
def test_package_does_not_import_the_call_allocator(package):
    offenders = []
    for path in python_files(package):
        for imported in module_imports(path):
            if imported == ALLOCATOR_MODULE:
                offenders.append(f"{path.name} imports {imported}")

    assert offenders == [], (
        "Pacing and safety code must not be able to dial: " + "; ".join(offenders)
    )


def test_pacing_engine_has_no_database_or_repository_access():
    engine = APP_ROOT / "pacing" / "pacing_engine.py"

    for imported in module_imports(engine):
        assert not imported.startswith("app.repositories")
        assert imported != "app.db"


def test_allocator_requires_a_safety_decision():
    signature = inspect.signature(CallAllocator.allocate)
    annotation = signature.parameters["decision"].annotation

    assert "decision" in signature.parameters
    assert "approved_slots" not in signature.parameters
    assert annotation is not int
    assert "SafetyDecision" in str(annotation)


def test_allocator_allocates_the_approved_number_and_nothing_else():
    source = inspect.getsource(CallAllocator.allocate)

    assert "decision.approved" in source
    assert "request.requested" not in source


def test_dialers_route_through_the_safety_controller_before_allocating():
    source = (APP_ROOT / "dialers" / "base.py").read_text(encoding="utf-8")

    safety_position = source.index("self._safety.evaluate")
    allocate_position = source.index("self._allocator.allocate")

    assert safety_position < allocate_position


def test_no_module_calls_allocate_with_a_bare_integer():
    offenders = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "allocate":
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, int):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
