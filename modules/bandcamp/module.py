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

from woob.capabilities.audio import Album, BaseAudio, CapAudio
from woob.tools.backend import Module

from .browser import BandcampBrowser


__all__ = ["BandcampModule"]


class BandcampModule(Module, CapAudio):
    NAME = "bandcamp"
    DESCRIPTION = "Bandcamp music website"
    MAINTAINER = "Vincent A"
    EMAIL = "dev@indigo.re"
    LICENSE = "AGPLv3+"
    VERSION = "3.7"

    BROWSER = BandcampBrowser

    def get_album(self, _id):
        _, band, album = _id.split(".")
        return self.browser.fetch_album_by_id(band, album)

    def get_audio(self, _id):
        _, band, track = _id.split(".")
        return self.browser.fetch_track_by_id(band, track)

    def search_album(self, pattern, sortby=0):
        for obj in self.browser.do_search(pattern):
            if isinstance(obj, Album):
                yield self.browser.fetch_album(obj)

    def search_audio(self, pattern, sortby=0):
        for obj in self.browser.do_search(pattern):
            if isinstance(obj, BaseAudio):
                yield self.browser.fetch_track(obj)
