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
from woob.tools.config.sqliteconfig import SQLiteConfig


@pytest.fixture
def empty_db(tmp_path: pathlib.Path) -> SQLiteConfig:
    """An empty configuration database."""
    config_path = tmp_path / "empty.db"
    config_path.write_bytes(b"")
    return SQLiteConfig(str(config_path))


@pytest.fixture
def existing_db(tmp_path: pathlib.Path) -> SQLiteConfig:
    """A configuration database with some configuration."""
    config_path = tmp_path / "config.db"
    config_path.write_bytes(b"")
    config = SQLiteConfig(str(config_path))
    with config:
        config.set("table1", "applications", "bill", {})
        config.set("table1", "applications", "float", 1.66)
    return config


def test_count_empty(empty_db: SQLiteConfig) -> None:
    """Count empty base."""
    config = empty_db
    with config:
        assert config.tables() == []

        config.ensure_table("example_table")
        assert config.tables() == ["example_table"]
        assert config.count("example_table") == 0


def test_config_delete_key(existing_db: SQLiteConfig) -> None:
    """Delete configuration keys."""
    config = existing_db
    with config:
        with pytest.raises(ConfigError):
            config.delete("table1", "applications.nonexisting")

        config.delete("table1", "applications.bill")
        assert config.count("table1") == 1


def test_config_get_key(existing_db: SQLiteConfig) -> None:
    """Get configuration keys."""
    config = existing_db
    with config:
        with pytest.raises(ConfigError):
            assert config.get("table1", "applications.nonexisting")

        assert config.get("table1", "applications.bill") == {}
        assert config.get("table1", "applications", "float") == 1.66
        assert config.get("table1", "applications.random", default="default") == "default"
        assert config.count("table1") == 2


def test_config_set_key(empty_db: SQLiteConfig) -> None:
    """Set configuration keys."""
    config = empty_db
    with config:
        with pytest.raises(ConfigError, match=r"A minimum of two levels are required."):
            config.set("S", 0)  # type: ignore[call-arg, arg-type]

        config.set("S", "applications", 0)
        assert config.count("S") == 1


def test_config_delete_table(existing_db: SQLiteConfig) -> None:
    """Can delete a table."""
    config = existing_db
    with config:
        with pytest.raises(ConfigError):
            config.delete("fail")

        config.delete("table1")


def test_config_keys_items(existing_db: SQLiteConfig) -> None:
    """Can list configuration items of a table."""
    config = existing_db
    with config:
        assert list(config.keys("table1")) == ["applications.bill", "applications.float"]
        assert list(config.items("table1", 1)) == [("applications.bill", {}), ("applications.float", 1.66)]


def test_delete_virtual_dict(existing_db: SQLiteConfig) -> None:
    """Delete configuration through VirtualDict."""
    config = existing_db
    with config:
        table = config.get("table1")

    with pytest.raises(KeyError, match=r"applications.nonexisting key in table1"):
        del table["applications.nonexisting"]

    del table["applications.bill"]
    assert len(table) == 1


def test_get_virtual_dict(existing_db: SQLiteConfig) -> None:
    """Get configuration through VirtualDict"""
    config = existing_db
    with config:
        table = config.get("table1")

    with pytest.raises(KeyError, match=r"applications.nonexisting key in table1"):
        assert table["applications.nonexisting"]

    assert table["applications.bill"] == {}
    assert table["applications.float"] == 1.66
    assert table.get("applications.random", default="default") == "default"
    assert len(table) == 2


def test_set_virtual_dict(empty_db: SQLiteConfig) -> None:
    """Set configuration through VirtualDict"""
    config = empty_db
    with config:
        table = config.get("S")

    table["applications"] = 0
    assert len(table) == 1


def test_in_virtual_dict(existing_db: SQLiteConfig) -> None:
    """Can check key existence through VirtualDict interface."""
    config = existing_db
    with config:
        table = config.get("table1")

    assert ("applications.nonexisting" in table) is False
    assert ("applications.bill" in table) is True
    assert (None in table) is False


def test_items_virtual_dict(existing_db: SQLiteConfig) -> None:
    """All items can be retrieved through VirtualDict interface."""
    config = existing_db
    with config:
        table = config.get("table1")

    assert list(table.items()) == [("applications.bill", {}), ("applications.float", 1.66)]
