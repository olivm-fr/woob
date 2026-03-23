# Copyright(C) 2010-2011 Christophe Benz, Romain Bignon
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

from .iformatter import IFormatter


__all__ = ["FormattersLoader", "FormatterLoadError"]


class FormatterLoadError(Exception):
    pass


class FormattersLoader:
    BUILTINS = ["htmltable", "multiline", "simple", "table", "csv", "json", "json_line"]

    def __init__(self) -> None:
        self.formatters: dict[str, type[IFormatter]] = {}

    def register_formatter(self, name: str, klass: type[IFormatter]) -> None:
        self.formatters[name] = klass

    def get_available_formatters(self) -> list[str]:
        formatters = set(self.formatters)
        formatters = formatters.union(self.BUILTINS)
        return sorted(formatters)

    def build_formatter(self, name: str) -> IFormatter:
        if name not in self.formatters:
            try:
                self.formatters[name] = self.load_builtin_formatter(name)
            except ImportError as e:
                FormattersLoader.BUILTINS.remove(name)
                raise FormatterLoadError(f'Unable to load formatter "{name}": {e}') from e
        return self.formatters[name]()

    def load_builtin_formatter(self, name: str) -> type[IFormatter]:
        if name not in self.BUILTINS:
            raise FormatterLoadError(f'Formatter "{name}" does not exist')

        if name == "htmltable":
            from .table import HTMLTableFormatter

            return HTMLTableFormatter
        elif name == "table":
            from .table import TableFormatter

            return TableFormatter
        elif name == "simple":
            from .simple import SimpleFormatter

            return SimpleFormatter
        elif name == "multiline":
            from .multiline import MultilineFormatter

            return MultilineFormatter
        elif name == "csv":
            from .csv import CSVFormatter

            return CSVFormatter
        elif name == "json":
            from .json import JsonFormatter

            return JsonFormatter
        elif name == "json_line":
            from .json import JsonLineFormatter

            return JsonLineFormatter

        # Should never happen
        raise ValueError(f'Formatter "{name}" is not handled properly.')
