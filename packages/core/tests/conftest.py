"""Shared fixtures for core tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from model_builders import write_model  # noqa: E402


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    write_model(tmp_path)
    return tmp_path


@pytest.fixture
def scd2_model_dir(tmp_path: Path) -> Path:
    write_model(tmp_path, with_scd2=True)
    return tmp_path
