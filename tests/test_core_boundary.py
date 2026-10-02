"""Import boundary of the core configuration layer (config-file-sync, Allowed Dependencies).

Traceability: every test names the requirement IDs it proves at the start of its docstring.
"""

import ast
from pathlib import Path

import troupe.config
from troupe.config import scalars
from troupe.config.writeback import compare

CONFIG_PACKAGE = Path(troupe.config.__file__).parent
WRITEBACK_PACKAGE = "troupe.config.writeback"


def _imported_modules(source: Path) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_no_core_config_module_imports_the_writeback_package() -> None:
    """Req 2.5: core code compares values without depending on the write-back package."""
    core_modules = sorted(CONFIG_PACKAGE.glob("*.py"))
    assert core_modules

    offenders = {
        module.name: imported
        for module in core_modules
        for imported in _imported_modules(module)
        if imported == WRITEBACK_PACKAGE or imported.startswith(WRITEBACK_PACKAGE + ".")
    }

    assert offenders == {}


def test_writeback_compare_still_exports_the_core_scalar_equal() -> None:
    """Req 2.5: ``set`` and reconcile use one and the same typed comparison."""
    assert compare.scalar_equal is scalars.scalar_equal
