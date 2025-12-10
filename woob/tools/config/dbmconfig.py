# Copyright(C) 2016-2021 Edouard Lefebvre du Prey
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

import dbm.ndbm
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import yaml
from typing_extensions import Unpack

from .iconfig import ConfigError, GetArgs, IConfig, IConfigGet, SetArgs
from .yamlconfig import WoobDumper


__all__ = ["DBMConfig"]


class NDBMProtocol(MutableMapping[str, str | object]):
    def close(self) -> None: ...


class DBMConfig(IConfig):
    def __init__(self, path: str) -> None:
        self.path = path
        self._storage: NDBMProtocol | None = None

    @property
    def storage(self) -> NDBMProtocol:
        if self._storage is None:
            raise RuntimeError("DBMConfig is not loaded.")
        return self._storage

    def load(self, default: Mapping[str, Any] = {}) -> None:
        self._storage = cast(NDBMProtocol, dbm.ndbm.open(self.path, "c"))

    def save(self) -> None:
        if self.storage and hasattr(self.storage, "sync"):
            self.storage.sync()

    def get(self, *args: Unpack[GetArgs], **kwargs: Unpack[IConfigGet]) -> Any:
        key = ".".join(args)
        try:
            value = self.storage[key]
            assert isinstance(value, (str, bytes))
            value = yaml.load(value, Loader=yaml.SafeLoader)
        except KeyError as exc:
            if "default" in kwargs:
                value = kwargs.get("default")
            else:
                raise ConfigError() from exc
        except TypeError as exc:
            raise ConfigError() from exc
        return value

    def set(self, *args: Unpack[SetArgs]) -> None:
        key = ".".join(args[:-1])
        value = args[-1]
        try:
            self.storage[key] = yaml.dump(value, None, Dumper=WoobDumper, default_flow_style=False)
        except KeyError as exc:
            raise ConfigError() from exc
        except TypeError as exc:
            raise ConfigError() from exc

    def delete(self, *args: Unpack[GetArgs]) -> None:
        key = ".".join(args)
        try:
            del self.storage[key]
        except KeyError as exc:
            raise ConfigError() from exc
        except TypeError as exc:
            raise ConfigError() from exc
