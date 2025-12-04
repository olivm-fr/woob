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

import datetime
from unittest.mock import Mock

from woob.tools.config.util import time_buffer


def test_time_buffer_no_buffer() -> None:
    """Add no delay by default."""

    func = Mock()
    buffer = time_buffer()
    assert buffer.since_seconds is None

    buffered_func = buffer(func)
    dt_ref = datetime.datetime.now()
    assert buffer.last_run < dt_ref  # set in __init__

    buffered_func()
    func.assert_called()
    assert buffer.since_seconds is None
    assert buffer.last_run > dt_ref  # set after call

    buffered_func()
    assert func.call_count == 2


def test_time_buffer_300s_buffer_decorator() -> None:
    """Add 300s delay through decorator."""

    func = Mock()
    buffer = time_buffer(since_seconds=300)
    assert buffer.since_seconds == 300

    buffered_func = buffer(func)
    dt_ref = datetime.datetime.now()

    buffered_func()
    func.assert_not_called()
    assert buffer.last_run < dt_ref

    buffer.last_run = dt_ref + datetime.timedelta(seconds=301)
    buffered_func()
    func.assert_called_once()
    assert buffer.last_run > dt_ref

    buffered_func()
    func.assert_called_once()  # not enough time passed

    buffer.last_run = dt_ref + datetime.timedelta(seconds=601)
    buffered_func()
    assert func.call_count == 2


def test_time_buffer_300s_buffer_call() -> None:
    """Add 300s delay through decorated function call."""

    func = Mock()
    buffer = time_buffer()
    assert buffer.since_seconds is None

    buffered_func = buffer(func)
    dt_ref = datetime.datetime.now()

    buffered_func()
    func.assert_called_once()
    assert buffer.last_run > dt_ref

    buffered_func(since_seconds=300)
    func.assert_called_once()  # not enough time passed

    buffer.last_run = dt_ref + datetime.timedelta(seconds=301)
    buffered_func(since_seconds=300)
    assert func.call_count == 2

    buffered_func()  # call without argument are not buffered
    assert func.call_count == 3
