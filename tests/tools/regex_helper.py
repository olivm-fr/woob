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

from woob.tools.regex_helper import normalize


def test_normalize_anchored_pattern() -> None:
    """Anchors are stripped from pattern."""
    assert normalize("^Once upon a time, ... The end.$") == [("Once upon a time, ... The end.", [])]


def test_normalize_character_class() -> None:
    """Character class are replaced by generic matching placeholder value."""
    assert normalize(r"my tailor is \w+") == [
        ("my tailor is x", []),
    ]


def test_normalize_character_range() -> None:
    """Character class are replaced by generic matching placeholder value."""
    assert normalize(r"I have [2-9] forks and a spoon.") == [
        ("I have 2 forks and a spoon.", []),
    ]


def test_normalize_quantifier() -> None:
    """Character class are replaced by generic matching placeholder value."""
    assert normalize(r"It's over 90{3}!") == [
        ("It's over 9000!", []),
    ]


def test_normalize_unnamed_capturing_group() -> None:
    """Capturing groups are replaced by named format string placeholder."""
    assert normalize(r"my tailor is (rich)") == [
        ("my tailor is %(_0)s", ["_0"]),
    ]


def test_normalize_named_capturing_group() -> None:
    """Capturing groups are replaced by positional format string placeholder."""
    assert normalize(r"On (?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2}),") == [
        ("On %(year)s/%(month)s/%(day)s,", ["year", "month", "day"])
    ]


def test_normalize_capturing_nested_group() -> None:
    """Matching nested groups replace everything."""
    assert normalize(r"Dear ((Mr|Ms) (\w+)),") == [
        ("Dear %(_0)s,", ["_0"]),
    ]


def test_normalize_non_capturing_nested_group() -> None:
    """Non-matched nested groups are inner groups."""
    assert normalize(r"Dear (?:(Mr|Ms) (\w+)),") == [
        ("Dear %(_0)s %(_1)s,", ["_0", "_1"]),
    ]
