# Copyright(C) 2010-2011 Romain Bignon
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

from __future__ import annotations

import datetime
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypedDict,
    TypeVar,
)

from typing_extensions import Unpack

from .misc import to_unicode


if TYPE_CHECKING:
    from woob.core.requests import RequestsManager

__all__ = ["ValuesDict", "Value", "ValueBackendPassword", "ValueInt", "ValueFloat", "ValueBool", "ValueDate"]

ValuesDictType = TypeVar("ValuesDictType", bound="ValuesDict")
T = TypeVar("T")


class ValuesDict(OrderedDict[str, "Value[Any]"]):
    """Ordered dictionary which can take values in constructor.

    Example:
        >>> ValuesDict(Value('a', label='Test'), ValueInt('b', label='Test2'))  # doctest: +SKIP
        ValuesDict({'a': <woob.tools.value.Value object at 0x...>,
                    'b': <woob.tools.value.ValueInt object at 0x...>})
    """

    def __init__(self, *values: Value[Any]) -> None:
        super().__init__()
        for v in values:
            self[v.id] = v

    def with_values(self: ValuesDictType, *values: Value[Any]) -> ValuesDictType:
        """Get a copy of the object, with new values.

        :param values: The values to set.
        :return: The new values dictionary.
        """
        existing_values = {key: value for key, value in self.items()}
        existing_values.update({value.id: value for value in values})
        return self.__class__(*existing_values.values())

    def with_values_from(self: ValuesDictType, other: ValuesDict) -> ValuesDictType:
        """Get a copy of the object, with overrides from another values dictionary.

        Values from the other dictionary will override values from the
        current dictionary.

        :param other: the other dictionary to take values from.
        :return: The new values dictionary.
        """
        return self.with_values(*other.values())

    def without_values(self: ValuesDictType, *value_names: str) -> ValuesDictType:
        """Get a copy of the object, without values with the given names.

        This method will ignore value names that aren't present in the
        original dictionary.

        :param value_names: The name of the values to remove.
        :return: The new values dictionary.
        """
        existing_values = {key: value for key, value in self.items()}
        for value_name in value_names:
            existing_values.pop(value_name, None)

        return self.__class__(*existing_values.values())


class ValueKwargs(TypedDict, total=False):
    # Unfortunately, cannot make this Generic before Python 3.11
    # Refs: https://github.com/python/cpython/issues/89026
    id: str
    label: str
    description: str  # legacy, use label
    regexp: str
    tiny: bool
    transient: bool
    masked: bool
    required: bool


class ValueAnyKwargs(ValueKwargs, total=False):
    default: Any
    aliases: Mapping[str | Any, str | Any]  # legacy, use choices
    choices: Mapping[str | Any, str | Any] | Iterable[str | Any]
    value: Any


class Value(Generic[T]):
    """
    Value.

    :param label: human readable description of a value
    :param required: if ``True``, the backend can't load if the key isn't found in its configuration
    :param default: an optional default value, used when the key is not in config. If there is no default value and the key
                    is not found in configuration, the **required** parameter is implicitly set
    :param masked: if ``True``, the value is masked. It is useful for applications to know if this key is a password
    :param regexp: if specified, on load the specified value is checked against this regexp, and an error is raised if it doesn't match
    :param choices: if this parameter is set, the value must be in the list
    :param aliases: mapping of old choices values that should be accepted but not presented
    :param tiny: the value of choices can be entered by an user (as they are small)
    :param transient: this value is not persistent (asked only if needed)
    """

    def __init__(self, *args: Any, **kwargs: Unpack[ValueAnyKwargs]) -> None:
        if len(args) > 0:
            self.id = args[0]
        else:
            self.id = ""
        self.label = kwargs.get("label", kwargs.get("description", None))
        self.description = kwargs.get("description", kwargs.get("label", None))
        self.default = kwargs.get("default", None)
        if isinstance(self.default, str):
            self.default = to_unicode(self.default)
        self.regexp = self.get_normalized_regexp(kwargs.get("regexp", None))

        self.aliases: Mapping[str | T, str | T] | None = kwargs.get("aliases")

        self.choices: Mapping[str | T, str | T] | None
        _choices = kwargs.get("choices", None)
        if _choices is None or isinstance(_choices, dict):
            self.choices = _choices
        else:
            self.choices = OrderedDict((v, v) for v in _choices)

        self.tiny = kwargs.get("tiny", None)
        self.transient = kwargs.get("transient", None)
        self.masked = kwargs.get("masked", False)
        self.required = kwargs.get("required", self.default is None)
        self._value = kwargs.get("value", None)

    @staticmethod
    def get_normalized_regexp(regexp: str | None) -> str | None:
        """Return normalized regexp adding missing anchors"""

        if not regexp:
            return regexp
        if not regexp.startswith("^"):
            regexp = "^" + regexp
        if not regexp.endswith("$"):
            regexp += "$"
        return regexp

    def show_value(self, v: T) -> str | T:
        if self.masked:
            return ""
        else:
            return v

    def check_valid(self, v: str | T | None) -> None:
        """
        Check if the given value is valid.

        :raises: ValueError
        """
        if self.required and v is None:
            raise ValueError("Value is required and thus must be set")
        if v == self.default:
            return
        if v == "" and self.default != "" and (self.choices is None or v not in self.choices):
            raise ValueError("Value can't be empty")
        if self.regexp is not None and not re.match(self.regexp, str(v) if v is not None else ""):
            raise ValueError('Value does not match regexp "%s"' % self.regexp)
        if self.choices is not None and v not in self.choices:
            if not self.aliases or v not in self.aliases:
                raise ValueError("Value is not in list: %s" % (", ".join(str(s) for s in self.choices)))

    def load(self, domain: str | None, v: T, requests: RequestsManager | None) -> None:
        """
        Load value.

        :param domain: what is the domain of this value
        :param v: value to load
        :param requests: list of woob requests
        """
        return self.set(v)

    def set(self, v: str | T | None) -> None:
        """
        Set a value.
        """
        self.check_valid(v)
        if v and self.aliases and v in self.aliases:
            v = self.aliases[v]
        self._value = v

    def dump(self) -> T | None:
        """
        Dump value to be stored.
        """
        return self.get()

    def get(self) -> T | None:
        """
        Get the value.
        """
        return self._value


class ValueTransient(Value[T]):
    def __init__(self, *args: Any, **kwargs: Unpack[ValueAnyKwargs]) -> None:
        kwargs.setdefault("transient", True)
        kwargs.setdefault("default", None)
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def dump(self) -> T | None:
        return ""


class ValuePasswordKwargs(ValueKwargs, total=False):
    default: str
    aliases: Mapping[str, str]  # legacy, use choices
    choices: Mapping[str, str] | Iterable[str]
    value: str
    noprompt: bool


class ValueBackendPassword(Value[str]):
    _domain = None
    _requests: RequestsManager | None = None
    _stored = True

    def __init__(self, *args: Any, **kwargs: Unpack[ValuePasswordKwargs]) -> None:
        kwargs.setdefault("default", "")
        kwargs.setdefault("masked", True)
        new_kwargs: dict[str, Any] = dict(kwargs)
        self.noprompt = new_kwargs.pop("noprompt", False)
        super().__init__(*args, **new_kwargs)

    def load(self, domain: str | None, password: str, requests: RequestsManager | None) -> None:
        self.check_valid(password)
        self._domain = domain
        self._value = to_unicode(password)
        self._requests = requests

    def check_valid(self, passwd: str | None) -> None:
        if passwd == "":
            # always allow empty passwords
            return True
        return super().check_valid(passwd)

    def set(self, passwd: str | None) -> None:
        self.check_valid(passwd)
        if passwd is None:
            # no change
            return
        self._value = ""
        if passwd == "":
            return
        if self._domain is None:
            self._value = to_unicode(passwd)
            return

        self._value = to_unicode(passwd)

    def dump(self) -> str | None:
        if self._stored:
            return self._value
        else:
            return ""

    def get(self) -> str | None:
        if self._value != "" or self._domain is None:
            return self._value

        passwd = None

        if passwd is not None:
            # Password has been read in the keyring.
            return to_unicode(passwd)

        # Prompt user to enter password by hand.
        if not self.noprompt and self._requests:
            self._value = self._requests.request("login", self._domain, self)
            if self._value is None:
                self._value = ""
            else:
                self._value = to_unicode(self._value)
                self._stored = False
        return self._value


class ValueIntKwargs(ValueKwargs, total=False):
    default: int
    aliases: Mapping[str | int, str | int]
    choices: Mapping[str | int, str | int] | Iterable[str | int]
    value: int


class ValueInt(Value[int]):
    def __init__(self, *args: Any, **kwargs: Unpack[ValueIntKwargs]) -> None:
        kwargs["regexp"] = r"^\d+$"
        kwargs.setdefault("default", 0)
        super().__init__(*args, **kwargs)

    def get(self) -> int | None:
        if self._value:
            return int(self._value)
        return None


class ValueFloatKwargs(ValueKwargs, total=False):
    default: float
    aliases: Mapping[str | float, str | float]
    choices: Mapping[str | float, str | float] | Iterable[str | float]
    value: float


class ValueFloat(Value[float]):
    def __init__(self, *args: Any, **kwargs: Unpack[ValueFloatKwargs]) -> None:
        kwargs["regexp"] = r"^[\d\.]+$"
        kwargs.setdefault("default", 0.0)
        super().__init__(*args, **kwargs)

    def check_valid(self, v: str | float | None) -> None:
        try:
            if v:
                float(v)
        except ValueError:
            raise ValueError("Value is not a float value")

    def get(self) -> float | None:
        if self._value:
            return float(self._value)
        return None


class ValueBoolKwargs(ValueKwargs, total=False):
    default: bool
    aliases: Mapping[str | bool, str | bool]
    choices: Mapping[str | bool, str | bool] | Iterable[str | bool]
    value: bool


class ValueBool(Value[bool]):
    def __init__(self, *args: Any, **kwargs: Unpack[ValueBoolKwargs]) -> None:
        kwargs["choices"] = {"y": "True", "n": "False"}
        kwargs.setdefault("default", False)
        super().__init__(*args, **kwargs)

    def check_valid(self, v: str | bool | None) -> None:
        if not isinstance(v, bool) and str(v).lower() not in {
            "y",
            "yes",
            "1",
            "true",
            "on",
            "n",
            "no",
            "0",
            "false",
            "off",
        }:

            raise ValueError("Value is not a boolean (y/n)")

    def get(self) -> bool:
        return (isinstance(self._value, bool) and self._value) or str(self._value).lower() in {
            "y",
            "yes",
            "1",
            "true",
            "on",
        }


class ValueDateKwargs(ValueKwargs, total=False):
    default: datetime.date
    aliases: Mapping[str | datetime.date, str | datetime.date]
    choices: Mapping[str | datetime.date, str | datetime.date] | Iterable[str | datetime.date]
    formats: Iterable[str]
    value: datetime.date


class ValueDate(Value[datetime.date]):
    DEFAULT_FORMAT = "%Y-%m-%d"

    def __init__(self, *args: Any, **kwargs: Unpack[ValueDateKwargs]) -> None:
        formats = tuple(kwargs.get("formats", ()))
        new_kwargs: dict[str, Any] = dict(kwargs)
        new_kwargs.pop("formats", None)
        super().__init__(*args, **new_kwargs)

        if formats:
            self.preferred_format = formats[0]
        else:
            self.preferred_format = self.DEFAULT_FORMAT
        self.accepted_formats = (self.DEFAULT_FORMAT,) + formats

    def _parse(self, v: str) -> datetime.date:
        for format in self.accepted_formats:
            try:
                dateval = datetime.datetime.strptime(v, format).date()
            except ValueError:
                continue
            return dateval

        raise ValueError("Value does not match format in %s" % self.accepted_formats)

    def check_valid(self, v: str | datetime.date | None) -> None:
        if self.required and not v:
            raise ValueError("Value is required and thus must be set")

    def load(self, domain: str | None, v: str | datetime.date | None, requests: RequestsManager | None) -> None:
        self.check_valid(v)
        if not v:
            self._value = None
            return
        if isinstance(v, str):
            v = self._parse(v)
        if isinstance(v, datetime.date):
            self._value = v
        else:
            raise ValueError("Value is not of the proper type")

    def dump(self) -> datetime.date | None:
        if self._value:
            assert isinstance(self._value, datetime.date)
            return self._value.strftime(self.DEFAULT_FORMAT)
        return None

    def set(self, v: str | datetime.date | None) -> None:
        self.load(None, v, None)

    def get_as_string(self) -> str | None:
        if self._value is None:
            return None

        assert isinstance(self._value, datetime.date)
        return self._value.strftime(self.preferred_format)
