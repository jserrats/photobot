from pathlib import Path

import pytest


@pytest.fixture
def download_dir(tmp_path: Path) -> Path:
    d = tmp_path / "files"
    d.mkdir()
    return d
