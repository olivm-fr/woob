# Copyright(C) 2017      Vincent A
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.


from woob.capabilities.translate import CapTranslate
from woob.tools.backend import Module

from .browser import LarousseBrowser


__all__ = ["LarousseModule"]


class LarousseModule(Module, CapTranslate):
    NAME = "larousse"
    DESCRIPTION = "larousse dictionary translations"
    MAINTAINER = "Vincent A"
    EMAIL = "dev@indigo.re"
    LICENSE = "AGPLv3+"
    VERSION = "3.7"

    BROWSER = LarousseBrowser

    def translate(self, source_language, destination_language, request):
        """
        Perfom a translation.

        :param source_language: language in which the request is written
        :param destination_language: language to translate the request into
        :param request: the sentence to be translated
        :rtype: Translation
        """
        return self.browser.translate(source_language, destination_language, request)
