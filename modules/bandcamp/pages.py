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

import json
import re

from woob.browser.elements import ItemElement, ListElement, method
from woob.browser.filters.html import AbsoluteLink, Attr
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, Date, Env, Field, Format, Regexp
from woob.browser.pages import HTMLPage, pagination
from woob.capabilities.audio import Album, BaseAudio
from woob.capabilities.base import NotAvailable
from woob.capabilities.collection import Collection


class ReleasesPage(HTMLPage):
    def do_stuff(self, _id):
        raise NotImplementedError()


class SearchPage(HTMLPage):
    @pagination
    @method
    class iter_content(ListElement):
        next_page = AbsoluteLink('//a[has-class("next")]')

        class iter_albums(ListElement):
            item_xpath = '//ul[@class="result-items"]/li[.//div[@class="itemtype"][normalize-space(text())="ALBUM"]]'

            class item(ItemElement):
                klass = Album

                obj_title = CleanText('.//div[@class="heading"]/a')
                obj_url = Regexp(AbsoluteLink('.//div[@class="heading"]/a'), r"^([^?]+)\?")
                obj_id = Regexp(Field("url"), r"://([-\w]+)\.bandcamp.com/album/([-\w]+)", r"album.\1.\2", default=None)

        class iter_tracks(ListElement):
            item_xpath = '//ul[@class="result-items"]/li[.//div[@class="itemtype"][normalize-space(text())="TRACK"]]'

            class item(ItemElement):
                klass = BaseAudio

                obj_title = CleanText('.//div[@class="heading"]/a')
                obj__page_url = Regexp(AbsoluteLink('.//div[@class="heading"]/a'), r"^([^?]+)\?")
                obj_id = Regexp(
                    Field("_page_url"), r"://([-\w]+)\.bandcamp.com/track/([-\w]+)", r"audio.\1.\2", default=None
                )

        class iter_artists(ListElement):
            item_xpath = '//ul[@class="result-items"]/li[.//div[@class="itemtype"][normalize-space(text())="ARTIST"]]'

            class item(ItemElement):
                klass = Collection

                obj_title = CleanText('.//div[@class="heading"]/a')
                obj_url = Regexp(AbsoluteLink('.//div[@class="heading"]/a'), r"^([^?]+)\?")
                obj_id = Regexp(Field("url"), r"://([-\w]+)\.bandcamp.com", r"artist.\1", default=None)

                def obj_split_path(self):
                    url = self.obj_url(self)
                    return [re.search(r"https://([^.]+)\.", url).group(1)]


class AlbumsPage(HTMLPage):
    def get_artist(self):
        return CleanText('//p[@id="band-name-location"]/span[@class="title"]')(self.doc)

    @method
    class iter_albums(ListElement):
        item_xpath = '//ol[has-class("music-grid")]/li'

        class item(ItemElement):
            klass = Album

            obj_url = AbsoluteLink("./a")
            obj__thumbnail_url = Attr('./a/div[@class="art"]/img', "src")
            obj_title = CleanText('./a/p[@class="title"]', children=False)
            obj_id = Format("album.%s.%s", Env("band"), Regexp(Field("url"), r"/album/([-\w]+)"))

            def obj_author(self):
                return CleanText('./a/p[@class="title"]/span[@class="artist-override"]')(self) or self.page.get_artist()


class AlbumPage(HTMLPage):
    @method
    class get_album(ItemElement):
        klass = Album

        def parse(self, el: ItemElement) -> None:
            """Extract embedded data sources in HTML content."""
            info = json.loads(CleanText('//script[@type="application/ld+json"]')(self))
            self.env["datePublished"] = Dict("datePublished")(info)
            self.env["author"] = Dict("byArtist/name")(info)

        obj_id = Format("album.%s.%s", Env("band"), Env("album"))
        obj_title = CleanText('//h2[@class="trackTitle"]')
        obj_author = Env("author")

        def obj_year(self):
            return Date().filter(Env("datePublished")(self)).year

        def obj_url(self):
            return self.page.url

    @method
    class iter_tracks(ListElement):
        item_xpath = '//table[@id="track_table"]/tr[has-class("track_row_view")]'

        def parse(self, el: ItemElement) -> None:
            """Extract embedded data sources in HTML content."""
            self.env["trackinfo"] = json.loads(
                CleanText().clean(Attr("//script[@data-tralbum]", "data-tralbum")(self))
            )["trackinfo"]

        class item(ItemElement):
            klass = BaseAudio

            def parse(self, el: ItemElement) -> None:
                track_num = int(el.get("rel").split("=")[1])
                track = Env("trackinfo")(self)[track_num - 1]
                self.env["url"] = Dict("file/mp3-128", default=NotAvailable)(track)
                self.env["duration"] = int(track["duration"])

            obj_title = CleanText('./td[@class="title-col"]//a')
            obj_ext = "mp3"
            obj_format = "mp3"
            obj_bitrate = 128
            obj__page_url = AbsoluteLink('./td[@class="title-col"]//a')
            obj_id = Format("audio.%s.%s", Env("band"), Regexp(Field("_page_url"), r"/track/([-\w]+)"))

            obj_duration = Env("duration")
            obj_url = Env("url")


class TrackPage(HTMLPage):
    @method
    class get_track(ItemElement):
        klass = BaseAudio

        def parse(self, el: ItemElement) -> None:
            """Extract embedded data sources in HTML content."""
            info = json.loads(CleanText('//script[@type="application/ld+json"]')(self))
            self.logger.info("%s", info)
            self.env["author"] = Dict("byArtist/name")(info)
            trackinfo = json.loads(CleanText().clean(Attr("//script[@data-tralbum]", "data-tralbum")(self)))[
                "trackinfo"
            ]
            track = trackinfo[0]
            self.env["url"] = Dict("file/mp3-128", default=NotAvailable)(track)
            self.env["duration"] = int(track["duration"])

        obj_id = Format("audio.%s.%s", Env("band"), Env("track"))
        obj_title = CleanText('//h2[@class="trackTitle"]')
        obj_author = Env("author")
        obj_ext = "mp3"
        obj_format = "mp3"
        obj_bitrate = 128

        obj_duration = Env("duration")
        obj_url = Env("url")

        def obj__page_url(self):
            return self.page.url
