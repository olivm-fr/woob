# Copyright(C) 2010-2021 Romain Bignon
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

from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, NoReturn
from unittest import TestCase, TestResult
from unittest.case import SkipTest

from woob.capabilities.base import empty
from woob.core import Woob


if TYPE_CHECKING:
    from woob.capabilities.base import BaseObject
    from woob.tools.backend import Module

__all__ = ["BackendTest", "SkipTest", "skip_without_config"]


class BackendTest(TestCase):
    MODULE: str

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.backends = {}
        self.backend_instance = None
        self.backend: Module | None = None
        self.woob = Woob()

        # Skip tests when passwords are missing
        self.woob.requests.register("login", self.login_cb)

        if self.woob.load_backends(modules=[self.MODULE]):
            # provide the tests with all available backends
            self.backends = self.woob.backend_instances

    def login_cb(self, backend_name: str, value: Any) -> NoReturn:
        raise SkipTest("missing config '%s' is required for this test" % value.label)

    def run(self, result: TestResult | None = None) -> TestResult | None:
        """
        Call the parent run() for each backend instance.
        Skip the test if we have no backends.
        """
        try:
            if not len(self.backends):
                self.backend = self.woob.build_backend(self.MODULE, nofail=True)
                return super().run(result)
            else:
                # Run for all backend
                for backend_instance in self.backends.keys():
                    print(backend_instance)
                    self.backend = self.backends[backend_instance]
                    return super().run(result)
        finally:
            self.woob.deinit()
        return None  # should be unreachable

    def shortDescription(self) -> str:
        """
        Generate a description with the backend instance name.
        """
        # do not use TestCase.shortDescription as it returns None
        return f"{str(self)} [{self.backend_instance}]"

    def is_backend_configured(self) -> bool:
        """
        Check if the backend is in the user configuration file
        """
        if self.backend:
            return self.woob.backends_config.backend_exists(self.backend.config.instname)
        return False

    def assertNotEmpty(self, obj: BaseObject, *args: Any) -> None:
        """
        Assert an object is neither `empty` in the BaseObject parlance.

        `obj` should not be `None`, `NotLoaded`, or `NotAvailable`.
        """
        self.assertFalse(empty(obj), *args)


def skip_without_config(*keys: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to skip a test if backend config is missing

    :param keys: if any of these keys is missing in backend config, skip test. Can be empty.
    """

    for key in keys:
        if callable(key):
            raise TypeError("skip_without_config() must be called with arguments")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(self: BackendTest, *args: Any, **kwargs: Any) -> Any:
            assert self.backend is not None, f"{self!r} must have valid backends defined"
            config = self.backend.config
            if not self.is_backend_configured():
                raise SkipTest("a backend must be declared in configuration for this test")
            for key in keys:
                if not config[key].get():
                    raise SkipTest("config key %r is required for this test" % key)

            return func(self, *args, **kwargs)

        return wrapper

    return decorator
