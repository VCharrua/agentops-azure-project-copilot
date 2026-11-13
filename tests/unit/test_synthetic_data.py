"""The committed dataset must be exactly what the generator produces, and must stay synthetic."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import DATA_DIR, REPO_ROOT


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_synthetic_data", REPO_ROOT / "scripts" / "generate_synthetic_data.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def test_committed_data_matches_the_generator(generator: ModuleType) -> None:
    assert generator.main(["--out", str(DATA_DIR), "--check"]) == 0


def test_generator_writes_and_detects_drift(generator: ModuleType, tmp_path: Path) -> None:
    assert generator.main(["--out", str(tmp_path)]) == 0
    assert generator.main(["--out", str(tmp_path), "--check"]) == 0
    (tmp_path / "budget_lines.csv").write_text("changed", encoding="utf-8")
    (tmp_path / "documents" / "stray.md").write_text("x", encoding="utf-8")
    assert generator.main(["--out", str(tmp_path), "--check"]) == 1


def test_every_email_address_uses_a_reserved_example_domain() -> None:
    addresses: set[str] = set()
    for path in DATA_DIR.rglob("*"):
        if path.is_file():
            addresses |= set(re.findall(r"[\w.+-]+@[\w.-]+", path.read_text(encoding="utf-8")))
    assert addresses
    assert all(address.rstrip(".").endswith(".example") for address in addresses)


def test_dataset_contains_exactly_one_poisoned_document() -> None:
    poisoned = [
        path.name
        for path in (DATA_DIR / "documents").glob("*.md")
        if "ignore all previous instructions" in path.read_text(encoding="utf-8").lower()
    ]
    assert poisoned == ["PRJ-002-email-quickpave.md"]
