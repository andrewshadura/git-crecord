import os
import subprocess
import sys
from typing import Optional

import pytest

from git_crecord.util import system


@pytest.fixture
async def spawn_crecord(tmp_path):
    env = os.environ
    env['PYTHONPATH'] = os.getcwd()
    p = subprocess.Popen(
        [sys.executable, "-m", "git_crecord.main", "crecord", "-m", "message"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        cwd=tmp_path,
    )
    yield p
    p.terminate()
    p.wait(timeout=2)
    p.kill()
    p.communicate(timeout=1)


@pytest.fixture
def empty_git_repo(tmp_path):
    for command in [
        ["git", "init"],
        ["git", "config", "user.name", "Committer Name"],
        ["git", "config", "user.email", "name@example.org"],
        ["git", "config", "commit.gpgsign", "false"],
    ]:
        system(command, cwd=tmp_path)

    assert (tmp_path / '.git').is_dir()
    yield tmp_path


@pytest.fixture
def license_text() -> list[str]:
    with open("COPYING") as f:
        return f.readlines()
