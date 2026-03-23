# Copyright(C) 2025 Gilles Dartiguelongue
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import annotations

import logging
import pathlib

import pytest

from woob.core.repositories import Repositories, Repository


LOCAL_REPOSITORY = """
[DEFAULT]
name = local
update = 197001010000
maintainer = jdoe@test.com
signed = 0
key_update = 0
obsolete = 0
url = {url}
"""

MOD1_INDEX_ENTRY = """
[mod1]
version = 197001010000
capabilities = SampleCap
dependencies =
description = <unspecified>
maintainer = <unspecified> <<unspecified>>
license = <unspecified>
icon =
woob_spec =
"""

MOD1 = """
from woob.capabilities.base import Capability
from woob.tools.backend import Module

class SampleCap(Capability): ...


class Mod1Module(Module, SampleCap):
    NAME = "mod1"
"""


def test_local_repository_build(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Load local repository that needs to build its index."""
    repo_path = tmp_path / "local_repo"

    # Module module
    mod1_p = repo_path / "mod1"
    mod1_p.mkdir(parents=True)
    (mod1_p / "__init__.py").write_text(MOD1)
    (mod1_p / "requirements.txt").touch()

    # Single file module
    (repo_path / "mod2.py").write_text(MOD1)

    # Plain folder
    (repo_path / "mod3").mkdir()

    # Broken module
    (repo_path / "mod4").mkdir()
    (repo_path / "mod4" / "__init__.py").write_text("<")

    repo = Repository("file://" + str(repo_path))
    assert repo.local is True
    assert not (repo_path / "modules.list").exists()

    with caplog.at_level(logging.WARNING):
        repo.retrieve_index(None, None)  # type: ignore[arg-type]
        assert (
            caplog.records[0].message
            == "Unable to build module mod4: [SyntaxError] invalid syntax (__init__.py, line 1)"
        )
    assert len(repo.modules) == 1

    index = (repo_path / "modules.list").read_text()
    assert "[mod1]" in index
    assert "capabilities = SampleCap" in index


def test_local_repository_built(tmp_path: pathlib.Path) -> None:
    """Load local repository that has its index built."""
    repo_path = tmp_path / "local_repo"
    repo_path.mkdir(parents=True)
    (repo_path / "modules.list").write_text(
        "\n".join([LOCAL_REPOSITORY.format(url=f"file://{repo_path}"), MOD1_INDEX_ENTRY])
    )
    (repo_path / "mod1.py").write_text(MOD1)

    repo = Repository("file://" + str(repo_path))
    assert repo.local is True

    repo.retrieve_index(None, None)  # type: ignore[arg-type]
    assert len(repo.modules) == 1


def test_load_repositories(tmp_path: pathlib.Path) -> None:
    """Load repositories' content."""
    workdir = tmp_path / "work"
    datadir = tmp_path / "data"

    workdir.mkdir(parents=True)
    (workdir / "sources.list").write_text(f"file://{tmp_path}")

    mod_path = datadir / "modules" / "1.0" / "woob_modules"
    mod_path.mkdir(parents=True)
    (mod_path / "mod1.py").write_text(MOD1)

    repo_path = datadir / "repositories"
    repo_path.mkdir(parents=True)
    (repo_path / "00-file___local").write_text(
        "\n".join([LOCAL_REPOSITORY.format(url=f"file://{mod_path}"), MOD1_INDEX_ENTRY])
    )

    repos = Repositories(str(workdir), str(datadir), "1.0")
    assert repos.get_module_info("notfound") is None
    assert len(repos.get_all_modules_info()) == 1

    modinfo = repos.get_module_info("mod1")
    assert modinfo is not None
    assert modinfo.is_installed() is True
    # assert modinfo.is_local() is True
    assert modinfo.has_caps("SampleCap") is True
    assert modinfo.has_caps("AnotherCap") is False
