# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

from weboob.tools.backend import Module, BackendConfig
from weboob.tools.value import Value
from weboob.capabilities.video import CapVideo, BaseVideo

from .browser import PeertubeBrowser


__all__ = ['PeertubeModule']


class PeertubeModule(Module, CapVideo):
    NAME = 'peertube'
    DESCRIPTION = 'Peertube'
    MAINTAINER = 'Vincent A'
    EMAIL = 'dev@indigo.re'
    LICENSE = 'AGPLv3+'
    VERSION = '2.1'

    CONFIG = BackendConfig(
        Value('url', label='Base URL of the PeerTube instance'),
    )

    BROWSER = PeertubeBrowser

    def create_default_browser(self):
        return self.create_browser(self.config['url'].get())

    def get_video(self, id):
        return self.browser.get_video(id)

    def search_videos(self, pattern, sortby=CapVideo.SEARCH_RELEVANCE, nsfw=False):
        for video in self.browser.search_videos(pattern, sortby):
            if nsfw or not video.nsfw:
                yield video

    def fill_video(self, obj, fields):
        if set(('url', 'size')) & set(fields):
            self.browser.get_video(obj.id, obj)
        if 'thumbnail' in fields and obj.thumbnail:
            obj.thumbnail.data = self.browser.open(obj.thumbnail.url).content

    OBJECTS = {
        BaseVideo: fill_video,
    }
