# -*- coding: utf-8 -*-

# Copyright(C) 2012 Lucien Loiseau
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

from woob.browser.pages import HTMLPage
from woob.browser.elements import ItemElement, ListElement, method
from woob.capabilities.translate import Translation
from woob.browser.filters.standard import CleanText, Regexp, Env


class TranslatePage(HTMLPage):
    @method
    class get_translation(ListElement):
        item_xpath = '//table[@class="WRD" and not(@id)]/tr[@id]'

        class item(ItemElement):
            klass = Translation

            obj_id = Regexp(CleanText('./@id'), '.*:(.*)')
            obj_lang_src = Env('sl')
            obj_lang_dst = Env('tl')
            obj_text = CleanText('./td[@class="ToWrd"]', children=False)
