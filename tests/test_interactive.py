import subprocess
from pathlib import Path
from time import sleep

import pytest

from git_crecord.util import system


@pytest.fixture
def prepare_test_data(empty_git_repo: Path, license_text: list[str]):
    file = empty_git_repo / "COPYING"
    file.write_text("".join(license_text[0:5]))
    system(["git", "add", str(file)], cwd=empty_git_repo)
    file.write_text("".join(license_text[0:10]))


def test_crecord(prepare_test_data, spawn_crecord: subprocess.Popen):
    spawn_crecord.communicate(input='c', timeout=10)
    sleep(3)
