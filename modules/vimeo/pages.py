# -*- coding: utf-8 -*-

# Copyright(C) 2010-2011 Romain Bignon
# Copyright(C) 2012 François Revol
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

from woob.capabilities.video import BaseVideo
from woob.capabilities.image import Thumbnail
from woob.browser.elements import ItemElement, method, DictElement
from woob.browser.pages import HTMLPage, pagination, JsonPage
from woob.browser.filters.standard import Regexp, CleanText
from woob.browser.filters.json import Dict


class ListPage(HTMLPage):
    def get_token(self):
        return Regexp(CleanText('//script'), '"jwt":"(.*)","url"', default=None)(self.doc)


class APIPage(JsonPage):
    @pagination
    @method
    class iter_videos(DictElement):
        item_xpath = 'data'

        next_page = Dict('paging/next')

        class item(ItemElement):
            klass = BaseVideo

            obj_id = Regexp(Dict('clip/uri'), '/videos/(.*)')
            obj_title = Dict('clip/name')

            def obj_thumbnail(self):
                thumbnail = Thumbnail(Dict('clip/pictures/sizes/0/link')(self))
                thumbnail.url = thumbnail.id
                return thumbnail
