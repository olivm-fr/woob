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


import itertools

from woob.browser import PagesBrowser
from woob.browser.exceptions import BrowserHTTPNotFound
from woob.browser.profiles import Firefox
from woob.browser.url import URL

from .pages import ArtistSongsPage, HomePage, ResultsPage, SongLyricsPage


__all__ = ["ParolesnetBrowser"]


class ParolesnetBrowser(PagesBrowser):
    PROFILE = Firefox()
    TIMEOUT = 30

    BASEURL = "http://www.paroles.net/"
    home = URL("$", HomePage)
    results = URL("search", ResultsPage)
    lyrics = URL("(?P<artistid>[^/]*)/paroles-(?P<songid>[^/]*)", SongLyricsPage)
    artist = URL("(?P<artistid>[^/]*)$", ArtistSongsPage)

    def iter_lyrics(self, criteria, pattern):
        self.home.stay_or_go()
        assert self.home.is_here()
        self.page.search_lyrics(pattern)
        assert self.results.is_here()
        if criteria == "song":
            return self.page.iter_song_lyrics()
        else:
            artist_ids = self.page.get_artist_ids()
            it = []
            # we just take the 3 first artists to avoid too many page loadings
            for aid in artist_ids[:3]:
                it = itertools.chain(it, self.artist.go(artistid=aid).iter_lyrics())
            return it

    def get_lyrics(self, id):
        ids = id.split("|")
        try:
            self.lyrics.go(artistid=ids[0], songid=ids[1])
            songlyrics = self.page.get_lyrics()
            return songlyrics
        except BrowserHTTPNotFound:
            return
