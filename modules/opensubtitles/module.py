# -*- coding: utf-8 -*-

# Copyright(C) 2013 Julien Veyssier
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

from urllib.parse import quote_plus

from woob.capabilities.subtitle import CapSubtitle, LanguageNotSupported, Subtitle
from woob.applications.subtitles.subtitles import LANGUAGE_CONV
from woob.tools.backend import Module

from .browser import OpensubtitlesBrowser


__all__ = ['OpensubtitlesModule']


class OpensubtitlesModule(Module, CapSubtitle):
    NAME = 'opensubtitles'
    MAINTAINER = u'Julien Veyssier'
    EMAIL = 'julien.veyssier@aiur.fr'
    VERSION = '3.6'
    DESCRIPTION = 'Opensubtitles subtitle website'
    LICENSE = 'AGPLv3+'
    BROWSER = OpensubtitlesBrowser

    def get_subtitle(self, id):
        return self.browser.get_subtitle(id)

    def get_subtitle_file(self, id):
        return self.browser.get_file(id)

    def iter_subtitles(self, language, pattern):
        if language not in LANGUAGE_CONV.keys():
            raise LanguageNotSupported()
        return self.browser.iter_subtitles(language, quote_plus(pattern.encode('utf-8')))

    def fill_subtitle(self, subtitle, fields):
        if 'description' in fields:
            sub = self.get_subtitle(subtitle.id)
            subtitle.description = sub.description

        return subtitle

    OBJECTS = {
        Subtitle: fill_subtitle,
    }
