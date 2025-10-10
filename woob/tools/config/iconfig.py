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

import types
from collections.abc import Mapping
from typing import Any, TypedDict

from typing_extensions import Unpack


class ConfigError(Exception):
    pass


class IConfigGet(TypedDict, total=False):
    default: Any


ConfigKeyPath = tuple[str, ...]
GetArgs = tuple[str, Unpack[ConfigKeyPath]]
SetArgs = tuple[str, Unpack[ConfigKeyPath], Any]


class IConfig:
    """
    Interface for config storage.

    Config stores keys and values. Each key is a path of components, allowing
    to group multiple options.
    """

    def load(self, default: Mapping[str, Any] = {}) -> None:
        """
        Load config.

        :param default: default values for the config
        """
        raise NotImplementedError()

    def save(self) -> None:
        """Save config."""
        raise NotImplementedError()

    def get(self, *args: Unpack[GetArgs], **kwargs: Unpack[IConfigGet]) -> Any:
        """
        Get the value of an option.

        :param args: path of the option key.
        :param default: if specified, default value when path is not found
        """
        raise NotImplementedError()

    def set(self, *args: Unpack[SetArgs]) -> None:
        """
        Set a config value.

        :param args: all args except the last arg are the path of the option key.
        """
        raise NotImplementedError()

    def delete(self, *args: Unpack[GetArgs]) -> None:
        """
        Delete an option from config.

        :param args: path to the option key.
        """
        raise NotImplementedError()

    def __enter__(self) -> None:
        self.load()

    def __exit__(self, t: type[BaseException], v: BaseException, tb: types.TracebackType) -> None:
        self.save()
