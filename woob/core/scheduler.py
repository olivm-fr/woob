# Copyright(C) 2010-2011 Romain Bignon, Christophe Benz
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


from threading import Event, RLock, Timer

from woob.tools.log import getLogger
from woob.tools.misc import get_backtrace


__all__ = ["Scheduler"]


class IScheduler:
    """Interface of a scheduler."""

    def schedule(self, interval, function, *args):
        """
        Schedule an event.

        :param interval: delay before calling the function
        :type interval: int
        :param function: function to call
        :type function: callabale
        :param args: arguments to give to function
        :returns: an event identificator
        """
        raise NotImplementedError()

    def repeat(self, interval, function, *args):
        """
        Repeat a call to a function

        :param interval: interval between two calls
        :type interval: int
        :param function: function to call
        :type function: callable
        :param args: arguments to give to function
        :returns: an event identificator
        """
        raise NotImplementedError()

    def cancel(self, ev):
        """
        Cancel an event

        :param ev: the event identificator
        """

        raise NotImplementedError()

    def run(self):
        """
        Run the scheduler loop
        """
        raise NotImplementedError()

    def want_stop(self):
        """
        Plan to stop the scheduler.
        """
        raise NotImplementedError()


class RepeatedTimer(Timer):
    def run(self):
        while not self.finished.is_set():
            try:
                self.function(*self.args, **self.kwargs)
            except Exception:
                # do not stop timer because of an exception
                print(get_backtrace())
            self.finished.wait(self.interval)
        self.finished.set()


class Scheduler(IScheduler):
    """Scheduler using Python's :mod:`threading`."""

    def __init__(self):
        self.logger = getLogger("%s.scheduler" % __name__)
        self.mutex = RLock()
        self.stop_event = Event()
        self.count = 0
        self.queue = {}

    def schedule(self, interval, function, *args):
        return self._schedule(Timer, interval, self._schedule_callback, function, *args)

    def repeat(self, interval, function, *args):
        return self._schedule(RepeatedTimer, interval, self._repeat_callback, function, *args)

    def _schedule(self, klass, interval, meta_func, function, *args):
        if self.stop_event.is_set():
            return

        with self.mutex:
            self.count += 1
            self.logger.debug(f'function "{function.__name__}" will be called in {interval} seconds')
            timer = klass(interval, meta_func, (self.count, interval, function, args))
            self.queue[self.count] = timer
            timer.start()
            return self.count

    def _schedule_callback(self, count, interval, function, args):
        with self.mutex:
            self.queue.pop(count)
        return function(*args)

    def _repeat_callback(self, count, interval, function, args):
        function(*args)
        with self.mutex:
            try:
                e = self.queue[count]
            except KeyError:
                return
            else:
                self.logger.debug(f'function "{function.__name__}" will be called in {e.interval} seconds')

    def cancel(self, ev):
        with self.mutex:
            try:
                e = self.queue.pop(ev)
            except KeyError:
                return False
            e.cancel()
            self.logger.debug('scheduled function "%s" is canceled' % e.function.__name__)
            return True

    def _wait_to_stop(self):
        self.want_stop()
        with self.mutex:
            for e in self.queue.values():
                e.cancel()
                e.join()
            self.queue = {}

    def run(self):
        try:
            while True:
                self.stop_event.wait(0.1)
        except KeyboardInterrupt:
            self._wait_to_stop()
            raise
        else:
            self._wait_to_stop()
        return True

    def want_stop(self):
        self.stop_event.set()
        with self.mutex:
            for t in self.queue.values():
                t.cancel()
                # Contrary to _wait_to_stop(), don't call t.join
                # because want_stop() have to be non-blocking.
            self.queue = {}
