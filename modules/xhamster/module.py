# -*- coding: utf-8 -*-

# Copyright(C) 2017      Roger Philibert
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


from woob.tools.backend import Module
from woob.capabilities.video import CapVideo, BaseVideo
from woob.capabilities.image import CapImage

from .browser import XHamsterBrowser


__all__ = ['XHamsterModule']


class XHamsterModule(Module, CapVideo):
    NAME = 'xhamster'
    DESCRIPTION = 'xhamster website'
    MAINTAINER = 'Roger Philibert'
    EMAIL = 'roger.philibert@gmail.com'
    LICENSE = 'AGPLv3+'
    VERSION = '3.6'

    BROWSER = XHamsterBrowser

    def get_video(self, _id):
        return self.browser.get_video(_id)

    def search_videos(self, pattern, sortby=CapImage.SEARCH_RELEVANCE, nsfw=False):
        if not nsfw:
            return []
        return self.browser.do_search(pattern)

    def fill_video(self, obj, fields):
        if 'url' in fields:
            new = self.browser.get_video(obj.id)
            obj.url = new.url
        if 'thumbnail' in fields:
            r = self.browser.open(obj.thumbnail.url)
            obj.thumbnail.data = r.content

    OBJECTS = {
        BaseVideo: fill_video,
    }
