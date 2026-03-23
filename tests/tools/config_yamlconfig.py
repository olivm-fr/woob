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

import pathlib

import pytest

from woob.tools.config.iconfig import ConfigError
from woob.tools.config.yamlconfig import YamlConfig


CONTENT = """
applications:
  bill: {}
  float: 1.66
"""


def test_load_file(tmp_path: pathlib.Path) -> None:
    """Load configuration settings from file."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(CONTENT)

    config = YamlConfig(str(config_path))
    config.load()

    assert config.get("applications", "float") == 1.66


def test_load_default(tmp_path: pathlib.Path) -> None:
    """Load configuration settings from provided defaults."""
    config = YamlConfig(str(tmp_path / "config.yml"))
    config.load({"key": "value"})

    assert config.get("key") == "value"


def test_get(tmp_path: pathlib.Path) -> None:
    """Get behavior with and without default."""
    config = YamlConfig(str(tmp_path / "config.yml"))

    # Missing top-level key returns None
    assert config.get("password") is None

    assert config.get("password", default="changeme") == "changeme"

    # Missing nested key
    with pytest.raises(ConfigError):
        config.get("account", "credentials", "password")

    # Nested key with a default value
    assert config.get("account", "credentials", "password", default="changeme") == "changeme"
