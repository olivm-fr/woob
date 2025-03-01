# Copyright(C) 2012-2021 Romain Bignon
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

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta

# because we don't want to import this file by "import json"
from decimal import Decimal
from typing import Any


__all__ = ["json", "mini_jsonpath"]

try:
    # try simplejson first because it is faster
    # However, note that simplejson has very different behaviors from the
    # stdlib json module. In particular, it is handling Decimal in a very
    # peculiar way and is not returning a string for them.
    import simplejson as json
except ImportError:
    # Python 2.6+ has a module similar to simplejson
    import json

from woob.capabilities.base import BaseObject, NotAvailable, NotLoaded


def mini_jsonpath(node: str | dict[Any, Any], path: str) -> Iterator[Any]:
    """
    Evaluates a dot separated path against JSON data. Path can contains
    star wilcards. Always returns a generator.

    Relates to https://goessner.net/articles/JsonPath/ but in a really basic
    and simpler form.

    >>> list(mini_jsonpath({"x": 95, "y": 77, "z": 68}, 'y'))
    [77]
    >>> list(mini_jsonpath({"x": {"y": {"z": "nested"}}}, 'x.y.z'))
    ['nested']
    >>> list(mini_jsonpath('{"data": [{"x": "foo", "y": 13}, {"x": "bar", "y": 42}, {"x": "baz", "y": 128}]}', 'data.*.y'))
    [13, 42, 128]
    """

    def iterkeys(i: dict[Any, Any] | list[Any]) -> list[int] | list[str]:
        # Wildcard operator applies to objects and arrays
        # https://www.rfc-editor.org/rfc/rfc9535.html#name-semantics-4
        if isinstance(i, list):
            return list(range(len(i)))
        return list(i.keys())

    def cut(s: str | None) -> tuple[str, str | None] | tuple[None, None]:
        if s:
            p = (s.split(".", 1) + [None])[:2]
            # mypy has trouble analyzing tuple size even with assertion assistance
            return tuple(p)  # type: ignore[return-value]
        return (None, None)

    if isinstance(node, str):
        node = json.loads(node)
    assert not isinstance(node, str)

    queue = [(node, cut(path))]
    while queue:
        node, (name, rest) = queue.pop(0)
        if name is None:
            yield node
            continue
        elif name == "*":
            keys = iterkeys(node)
        elif type(node) not in (dict, list) or name not in node:
            continue
        elif isinstance(node, list):
            keys = [int(name)]
        else:
            keys = [name]
        for k in keys:
            queue.append((node[k], cut(rest)))


class WoobEncoder(json.JSONEncoder):
    """JSON encoder class for woob objects (and Decimal and dates)

    >>> from woob.capabilities.base import BaseObject
    >>> obj = BaseObject(id="1234", backend="my")
    >>> json.dumps(obj, cls=WoobEncoder)
    '{"id": "1234@my", "url": null}'
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # avoid simplejson internal Decimal handling
        if "use_decimal" in kwargs:
            kwargs["use_decimal"] = False
        super().__init__(*args, **kwargs)

    def default(self, o: Any) -> Any:
        if o is NotAvailable:
            return None
        elif o is NotLoaded:
            return None
        elif isinstance(o, BaseObject):
            return o.to_dict()
        elif isinstance(o, Decimal):
            return str(o)
        elif isinstance(o, (datetime, date, time)):
            return o.isoformat()
        elif isinstance(o, timedelta):
            return o.total_seconds()
        return super().default(o)


WeboobEncoder = WoobEncoder
