import sys
from pathlib import Path

import pytest

# Shared model builder lives in core/tests; drift fixtures alongside this file.
_CORE_TESTS = Path(__file__).resolve().parents[2] / "core" / "tests"
sys.path.insert(0, str(_CORE_TESTS))
sys.path.insert(0, str(Path(__file__).parent))

from model_builders import write_model  # noqa: E402


@pytest.fixture
def model_dir(tmp_path):
    write_model(tmp_path)
    return tmp_path
