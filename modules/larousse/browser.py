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


from woob.browser import URL, PagesBrowser
from woob.capabilities.translate import LanguageNotSupported

from .pages import LangList, WordPage


class LarousseBrowser(PagesBrowser):
    BASEURL = "https://www.larousse.fr"

    langlist = URL("/dictionnaires/bilingues$", LangList)
    word = URL(r"/dictionnaires/(?P<src>\w+)-(?P<dst>\w+)/(?P<word>.*)", WordPage)

    LANGS = None

    def _init(self):
        if self.LANGS:
            return
        self.langlist.go()
        self.LANGS = self.page.get_langs()

    def translate(self, src, dst, word):
        self._init()
        try:
            nsrc, ndst = self.LANGS[src, dst]
        except KeyError:
            raise LanguageNotSupported()

        self.word.go(src=nsrc, dst=ndst, word=word)
        return self.page.iter_translations(src=src, dst=dst)
