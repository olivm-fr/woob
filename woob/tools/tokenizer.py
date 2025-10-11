# Copyright(C) 2014 Oleg Plakhotniuk
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

import re
from collections.abc import Iterable
from typing import Callable


__all__ = ["ReTokenizer"]


LexTable = Iterable[tuple[str, str]]


class ReTokenizer:
    """
    Simple regex-based tokenizer (AKA lexer or lexical analyser).
    Useful for PDF statements parsing.

    1. There's a lexing table consisting of type-regex tuples.
    2. Lexer splits text into chunks using the separator character.
    3. Text chunk is sequentially matched against regexes and first
       successful match defines the type of the token.

    Check out test() function below for examples.
    """

    def __init__(self, text: str, sep: str, lex: LexTable) -> None:
        self._lex = lex
        self._tok = [ReToken(lex, chunk) for chunk in text.split(sep)]

    def tok(self, index: int) -> ReToken:
        if 0 <= index < len(self._tok):
            return self._tok[index]
        else:
            return ReToken(self._lex, eof=True)

    def simple_read(
        self, token_type: str, pos: int, transform: Callable[[str | None], str | None] = lambda v: v
    ) -> tuple[int, str | None]:
        t = self.tok(pos)
        is_type = getattr(t, "is_%s" % token_type)()
        return (pos + 1, transform(t.value())) if is_type else (pos, None)


class ReToken:
    def __init__(self, lex: LexTable, chunk: str | None = None, eof: bool = False) -> None:
        self._lex = lex
        self._eof = eof
        self._value = None
        self._type = None
        if chunk is not None:
            for type_, regex in self._lex:
                m = re.match(regex, chunk, flags=re.UNICODE)
                if m:
                    self._type = type_
                    if len(m.groups()) == 1:
                        self._value = m.groups()[0]
                    elif m.groups():
                        self._value = m.groups()
                    else:
                        self._value = m.group(0)
                    break

    def is_eof(self) -> bool:
        return self._eof

    def value(self) -> str | None:
        return self._value

    def __getattr__(self, name: str) -> Callable[[], bool]:
        if name.startswith("is_"):
            return lambda: self._type == name[3:]
        raise AttributeError()
