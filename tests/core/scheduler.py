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

import logging
import time
from unittest.mock import Mock

import pytest

from woob.core.scheduler import Scheduler


def test_cancel_existing_event(caplog: pytest.LogCaptureFixture) -> None:
    """Cancel scheduled function."""
    func = Mock()
    func.__name__ = "myfunc"

    sched = Scheduler()
    try:
        sid = sched.schedule(0.01, func)

        assert sid == 1
        func.assert_not_called()

        with caplog.at_level(logging.DEBUG, logger="woob.core.scheduler.scheduler"):
            assert sched.cancel(sid) is True
            assert caplog.record_tuples == [
                ("woob.core.scheduler.scheduler", 10, 'scheduled function "_schedule_callback" is canceled'),
            ]

        time.sleep(0.011)
        func.assert_not_called()
    finally:
        sched.want_stop()


def test_cancel_missing_event(caplog: pytest.LogCaptureFixture) -> None:
    """Cancelling missing event returns False."""
    sched = Scheduler()
    try:
        assert sched.cancel(0) is False
    finally:
        sched.want_stop()


def test_schedule() -> None:
    """Scheduled function is called once."""
    func = Mock()
    func.__name__ = "myfunc"

    sched = Scheduler()
    try:
        sid = sched.schedule(0.01, func)

        assert sid == 1
        func.assert_not_called()
        time.sleep(0.011)
        func.assert_called_once()
        time.sleep(0.011)
        func.assert_called_once()
    finally:
        sched.want_stop()


def test_repeat(caplog: pytest.LogCaptureFixture) -> None:
    """Scheduled function is called once."""
    func = Mock()
    func.__name__ = "myfunc"

    sched = Scheduler()
    try:
        sid = sched.repeat(0.01, func)

        assert sid == 1

        with caplog.at_level(logging.DEBUG, logger="woob.core.scheduler.scheduler"):
            time.sleep(0.021)
            assert func.call_count >= 2  # time sensitive test might be 2 or 3
            assert caplog.record_tuples == [
                ("woob.core.scheduler.scheduler", logging.DEBUG, 'function "myfunc" will be called in 0.01 seconds'),
            ] * len(caplog.record_tuples)
    finally:
        sched.want_stop()
