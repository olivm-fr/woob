# Copyright(C) 2023 Powens
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

import datetime
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from woob.tools.backend import BackendConfig
from woob.tools.value import (
    Value,
    ValueBackendPassword,
    ValueBool,
    ValueDate,
    ValueFloat,
    ValueInt,
    ValuesDict,
    ValueTransient,
)


@pytest.mark.parametrize("cls", (ValuesDict, BackendConfig))
def test_with_values(cls: Callable[..., ValuesDict | BackendConfig]) -> None:
    """Test creating copies of dictionaries using with_values."""
    first_obj = cls(
        Value("a", label="A value"),
        Value("b", label="B value"),
    )
    second_obj = first_obj.with_values(
        Value("a", label="Different A value"),
        Value("c", label="C value"),
    )

    # Check that the first object hasn't changed, and that the second
    # object indeed is different.
    assert second_obj is not first_obj
    assert set(first_obj) == {"a", "b"}
    assert first_obj["a"].label == "A value"

    # Check that the second object is how we want it.
    assert set(second_obj) == {"a", "b", "c"}
    assert second_obj["a"] is not first_obj["a"]
    assert second_obj["b"] is first_obj["b"]  # No unnecessary copies.
    assert second_obj["a"].label == "Different A value"


@pytest.mark.parametrize("cls", (ValuesDict, BackendConfig))
def test_without_values(cls: Callable[..., ValuesDict | BackendConfig]) -> None:
    """Test creating copies of dictionaries using without_values."""
    first_obj = cls(
        Value("a", label="A value"),
        Value("b", label="B value"),
    )
    second_obj = first_obj.without_values("b")

    # Check that the first object hasn't changed, and that the second
    # object indeed is different.
    assert second_obj is not first_obj
    assert set(first_obj) == {"a", "b"}

    # Check that the second object is how we want it.
    assert set(second_obj) == {"a"}
    assert second_obj["a"] is first_obj["a"]


def test_value_default_id() -> None:
    """Value has an empty string if no ID is provided."""
    v: Value[Any] = Value()
    assert v.id == ""


def test_value_default_value() -> None:
    """Value equals default when no value is provided."""
    v: Value[int] = Value("a", default=1)
    assert v.required is False
    assert v.get() == None  # does not return default

    v.set(42)
    assert v.get() == 42  # returns value

    v.set(None)
    assert v.get() is None  # returns value


def test_value_required() -> None:
    """Value equals default when no value is provided."""
    v: Value[int] = Value("a", value=42)
    assert v.required is True
    assert v.transient is None
    with pytest.raises(ValueError):
        v.set(None)


def test_value_transient_attrs() -> None:
    """Cannot be dumped and has specific attributes."""
    v: ValueTransient[Any] = ValueTransient("t")
    assert v.required is False
    assert v.transient is True
    assert v.default is None
    assert v.dump() == ""


def test_value_password_attrs() -> None:
    """Has specific attributes."""
    v = ValueBackendPassword("p", value="changeme")
    assert v.masked is True
    assert v.default == ""
    assert v.get() == "changeme"
    assert v.dump() == "changeme"

    v.load(None, "ichangedit", None)
    assert v.get() == "ichangedit"


def test_value_password_set() -> None:
    """Password value can be anything but ``None``."""
    v = ValueBackendPassword("p")

    v.set("ichangedit")
    assert v.get() == "ichangedit"

    v.set(None)
    assert v.get() == "ichangedit"

    v.set("")
    assert v.get() == ""

    v.set(True)  # type: ignore[arg-type]
    assert v.get() == "True"

    v.set(10)  # type: ignore[arg-type]
    assert v.get() == "10"


def test_value_password_load_from_storage() -> None:
    """Password is loaded from storage when noprompt is False."""
    storage = Mock()
    storage.request.return_value = "stored-password"

    # Default noprompt=False, get password from storage
    v = ValueBackendPassword("p")
    v.load("DOMAIN", "", storage)

    assert v.get() == "stored-password"
    assert v.dump() == ""  # _stored turned False


def test_value_password_dumped() -> None:
    """Password can be dumped if noprompt is True."""
    storage = Mock()
    storage.request.return_value = "stored-password"

    # With noprompt=True, get password from load call
    v = ValueBackendPassword("p", noprompt=True)
    v.load("DOMAIN", "loadmypassword", storage)

    assert v.get() == "loadmypassword"
    assert v.dump() == "loadmypassword"


def test_value_int() -> None:
    """Value is int or None."""
    v = ValueInt("i")
    assert v.default == 0
    assert v.get() is None

    v.set(1)
    assert v.get() == 1

    v.set("489495646")
    assert v.get() == 489495646

    with pytest.raises(ValueError) as exc:
        v.set("abc")
        assert "Value does not match regexp" in str(exc)


def test_value_float() -> None:
    """Value is float or None."""
    v = ValueFloat("f")
    assert v.default == 0.0
    assert v.get() is None

    v.set(0.1)
    assert v.get() == 0.1

    v.set("1.23456")
    assert v.get() == 1.23456

    with pytest.raises(ValueError) as exc:
        v.set("abc")
        assert "Value does not match regexp" in str(exc)


def test_value_bool() -> None:
    """Value is bool or generally acceptable human bool equivalent."""
    v = ValueBool("b")
    assert v.default is False
    assert v.get() is False

    v.set(True)
    assert v.get() == True

    v.set("False")
    assert v.get() == False

    v.set("y")
    assert v.get() == True

    with pytest.raises(ValueError) as exc:
        v.set(None)
        assert str(exc) == "Value is not a boolean (y/n)"

    with pytest.raises(ValueError) as exc:
        v.set("abc")
        assert str(exc) == "Value is not a boolean (y/n)"


def test_value_date_formats() -> None:
    """Custom format is parsed and rebuilt through :meth:`ValueDate.get_as_string`."""
    v = ValueDate("d")  # default format
    assert v.get() is None
    assert v.get_as_string() is None
    assert v.dump() is None

    v.set("2025-02-06")
    assert v.get() == datetime.date(2025, 2, 6)
    assert v.dump() == "2025-02-06"
    assert v.get_as_string() == "2025-02-06"

    v = ValueDate("d", formats=("%d/%m/%Y",))
    v.set("2025-06-07")  # Using default format
    assert v.get() == datetime.date(2025, 6, 7)
    assert v.dump() == "2025-06-07"
    assert v.get_as_string() == "07/06/2025"
    v.set("08/06/2025")  # Using custom format
    assert v.get() == datetime.date(2025, 6, 8)
    assert v.dump() == "2025-06-08"
    assert v.get_as_string() == "08/06/2025"

    with pytest.raises(ValueError) as exc:
        v.set("09-06-2025")
        assert "Value does not match format in" in str(exc)
