import sys
from pathlib import Path

import pytest

_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))

from model_builders import write_model  # noqa: E402


@pytest.fixture
def model_dir(tmp_path):
    write_model(tmp_path)
    return tmp_path


@pytest.fixture
def client(model_dir):
    from fastapi.testclient import TestClient
    from mdl_server import create_app

    return TestClient(create_app(model_dir))
