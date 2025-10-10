# Copyright(C) 2010-2011 Christophe Benz
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

import logging
import time
from typing import Any, Callable, Protocol, TypeVar, cast


__all__ = ["retry"]

R = TypeVar("R")


class ExceptionHandler(Protocol):
    def __call__(self, exc: BaseException, **kwargs: Any) -> None: ...


# TODO: Revisit with ParamSpec
def retry(
    exceptions_to_check: type[BaseException] | tuple[type[BaseException], ...],
    exc_handler: ExceptionHandler | None = None,
    tries: int = 3,
    delay: float = 2,
    backoff: float = 2,
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """
    Retry decorator
    from https://www.saltycrane.com/blog/2009/11/trying-out-retry-decorator-python/
    original from https://wiki.python.org/moin/PythonDecoratorLibrary#Retry
    """

    def deco_retry(f: Callable[..., R]) -> Callable[..., R]:
        def f_retry(*args: Any, **kwargs: Any) -> R:
            mtries = cast(int, kwargs.pop("_tries", tries))
            mdelay = cast(float, kwargs.pop("_delay", delay))
            while mtries > 1:
                try:
                    return f(*args, **kwargs)
                except exceptions_to_check as exc:
                    if exc_handler:
                        exc_handler(exc, **kwargs)
                    logging.debug("%s, Retrying in %d seconds...", exc, mdelay)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
            return f(*args, **kwargs)

        return f_retry  # true decorator

    return deco_retry
