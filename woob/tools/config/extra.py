# Copyright(C) 2017-2021 Romain Bignon
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

import multiprocessing
import os
import types
from collections.abc import Mapping
from logging import Logger
from typing import Any

from .util import time_buffer


__all__ = ["AutoCleanConfig", "ForkingConfig", "TimeBufferConfig"]


"""
These classes add functionality to existing IConfig classes.
Example:

    class MyYamlConfig(TimeBufferConfig, ForkingConfig, YamlConfig):
        saved_since_seconds = 42

The recommended order is TimeBufferConfig, AutoCleanConfig, ForkingConfig, and then the
actual storage class.
"""


class AutoCleanConfig:
    """
    Removes config file if it has no values.
    """

    path: str
    values: dict[str, Any]

    def save(self) -> None:
        if self.values:
            super().save()
        else:
            try:
                os.remove(self.path)
            except OSError:
                pass


class ForkingConfig:
    """
    Runs the actual save in a forked processes, making save non-blocking.
    It prevents two save() from being called at once by blocking on the previous one
    if it is not finished.
    It is also possible to call join() to wait for the save to complete.
    """

    path: str
    process: multiprocessing.Process | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.lock = multiprocessing.RLock()
        super().__init__(*args, **kwargs)

    def join(self) -> None:
        with self.lock:
            if self.process:
                self.process.join()
            self.process = None

    def save(self) -> None:
        # if a save is already in progress, wait for it to finish
        self.join()

        parent_save = super().save
        with self.lock:
            self.process = multiprocessing.Process(target=parent_save, name="save %s" % self.path)
            self.process.start()

    def __exit__(self, t: type[BaseException], v: BaseException, tb: types.TracebackType) -> None:
        self.join()
        super().__exit__(t, v, tb)

    def __getstate__(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("lock", None)
        return d

    def __setstate__(self, d: Mapping[str, Any]) -> None:
        self.__init__(path=d["path"])
        for k, v in d.items():
            setattr(self, k, v)


class TimeBufferConfig:
    """
    Really saves only every saved_since_seconds seconds.
    It is possible to force save (e.g. at exit) with force_save().
    """

    saved_since_seconds = None

    def __init__(
        self,
        path: str,
        saved_since_seconds: int | None = None,
        last_run: bool = True,
        logger: Logger | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(path, *args, **kwargs)
        if saved_since_seconds:
            self.saved_since_seconds = saved_since_seconds
        if self.saved_since_seconds:
            self.save = time_buffer(since_seconds=self.saved_since_seconds, last_run=last_run, logger=logger)(self.save)

    def save(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("since_seconds", None)
        super().save(*args, **kwargs)

    def force_save(self) -> None:
        self.save(since_seconds=False)

    def __exit__(self, t, v, tb):
        self.force_save()
        super().__exit__(t, v, tb)

    def __getstate__(self) -> dict[str, Any]:
        try:
            d: dict[str, Any] = super().__getstate__()
        except AttributeError:
            d = self.__dict__.copy()
        # When decorated, it is not serializable.
        # The decorator will be added again by __setstate__.
        d.pop("save", None)
        return d

    def __setstate__(self, d: Mapping[str, Any]) -> None:
        # Add the decorator if needed
        self.__init__(path=d["path"], saved_since_seconds=d.get("saved_since_seconds"))
        for k, v in d.items():
            setattr(self, k, v)
