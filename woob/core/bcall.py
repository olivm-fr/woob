# Copyright(C) 2010-2014 Romain Bignon, Christophe Benz
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

import queue
from collections.abc import Iterable, Iterator
from copy import copy
from threading import Event, Thread
from typing import Any, Callable

from woob.capabilities.base import BaseObject
from woob.tools.backend import Module
from woob.tools.log import getLogger
from woob.tools.misc import get_backtrace


__all__ = ["BackendsCall", "CallErrors"]

CallError = tuple[Module, Exception, str]


class CallErrors(Exception):
    def __init__(self, errors: Iterable[CallError]) -> None:
        msg = "Errors during backend calls:\n" + "\n".join(
            [f"Module({backend!r}): {error!r}\n{backtrace!r}\n" for backend, error, backtrace in errors]
        )

        super().__init__(msg)
        self.errors = copy(errors)

    def __iter__(self) -> Iterator[CallError]:
        return self.errors.__iter__()


class BackendsCall:
    def __init__(
        self, backends: Iterable[Module], function: str | Callable[..., Any], *args: Any, **kwargs: Any
    ) -> None:
        """
        :param backends: List of backends to call
        :param function: backends' method name, or callable object.
        """
        self.logger = getLogger(__name__)

        self.responses: queue.Queue[Any] = queue.Queue()
        self.errors: list[CallError] = []
        self.tasks: queue.Queue[Module] = queue.Queue()
        self.stop_event = Event()
        self.threads = []

        for backend in backends:
            t = Thread(target=self.backend_process, args=(function, args, kwargs))
            t.start()
            self.threads.append(t)
            self.tasks.put(backend)

    def store_result(self, backend: Module, result: Any) -> None:
        """Store the result when a backend task finished."""
        if result is None:
            return

        if isinstance(result, BaseObject):
            result.backend = backend.name
        self.responses.put(result)

    def backend_process(
        self,
        function: str | Callable[..., Any],
        args: Any,
        kwargs: Any,
    ) -> None:
        """
        Internal method to run a method of a backend.

        As this method may be blocking, it should be run on its own thread.
        """
        backend = self.tasks.get()
        with backend:
            try:
                # Call method on backend
                try:
                    self.logger.debug("%s: Calling function %s", backend, function)
                    if callable(function):
                        result = function(backend, *args, **kwargs)
                    else:
                        result = getattr(backend, function)(*args, **kwargs)
                except Exception as error:
                    self.logger.debug("%s: Called function %s raised an error: %r", backend, function, error)
                    self.errors.append((backend, error, get_backtrace(str(error))))
                else:
                    self.logger.debug("%s: Called function %s returned: %r", backend, function, result)

                    if hasattr(result, "__iter__") and not isinstance(result, (bytes, str)):
                        # Loop on iterator
                        try:
                            for subresult in result:
                                self.store_result(backend, subresult)
                                if self.stop_event.is_set():
                                    break
                        except Exception as error:
                            self.errors.append((backend, error, get_backtrace(str(error))))
                    else:
                        self.store_result(backend, result)
            finally:
                self.tasks.task_done()

    def _callback_thread_run(
        self,
        callback: Callable[[Any], None] | None,
        errback: Callable[[Module, Exception, str], None] | None,
        finishback: Callable[[], None] | None,
    ) -> None:
        while not self.stop_event.is_set() and (self.tasks.unfinished_tasks or not self.responses.empty()):
            try:
                response = self.responses.get(timeout=0.1)
            except queue.Empty:
                continue
            else:
                if callback:
                    callback(response)

        # Raise errors
        while errback and self.errors:
            errback(*self.errors.pop(0))

        if finishback:
            finishback()

    def callback_thread(
        self,
        callback: Callable[[Any], None] | None,
        errback: Callable[[Module, Exception, str], None] | None = None,
        finishback: Callable[[], None] | None = None,
    ) -> Thread:
        """
        Call this method to create a thread which will callback a
        specified function everytimes a new result comes.

        When the process is over, the function will be called with
        both arguments set to None.

        The functions prototypes:
            def callback(result)
            def errback(backend, error, backtrace)
            def finishback()

        """
        thread = Thread(target=self._callback_thread_run, args=(callback, errback, finishback))
        thread.start()
        return thread

    def wait(self) -> None:
        """Wait until all tasks are finished."""
        for thread in self.threads:
            thread.join()

        if self.errors:
            raise CallErrors(self.errors)

    def stop(self, wait: bool = False) -> None:
        """
        Stop all tasks.

        :param wait: If True, wait until all tasks stopped.
        """

        self.stop_event.set()

        if wait:
            self.wait()

    def __iter__(self) -> Iterator[Any]:
        try:
            while not self.stop_event.is_set() and (self.tasks.unfinished_tasks or not self.responses.empty()):
                try:
                    yield self.responses.get(timeout=0.1)
                except queue.Empty:
                    continue
        except:
            self.stop()
            raise

        if self.errors:
            raise CallErrors(self.errors)
