# Copyright(C) 2018 Julien Veyssier
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


from woob.browser import PagesBrowser
from woob.browser.exceptions import BrowserHTTPNotFound
from woob.browser.profiles import Wget
from woob.browser.url import URL

from .pages import HomePage, SearchPage, TorrentPage


__all__ = ["BtmonBrowser"]


class BtmonBrowser(PagesBrowser):
    PROFILE = Wget()
    TIMEOUT = 30

    BASEURL = "http://www.btmon.com/"
    home = URL("$", HomePage)
    search = URL(r"/torrent/\?sort=relevance&f=(?P<pattern>.*)", SearchPage)
    torrent = URL(r"/(?P<torrent_id>.*)\.torrent\.html", TorrentPage)

    def get_bpc_cookie(self):
        if "BPC" not in self.session.cookies:
            self.home.go()
            bpcCookie = str(self.page.content).split("BPC=")[-1].split('"')[0]
            self.session.cookies["BPC"] = bpcCookie

    def iter_torrents(self, pattern):
        self.get_bpc_cookie()
        return self.search.go(pattern=pattern).iter_torrents()

    def get_torrent(self, id):
        try:
            self.get_bpc_cookie()
            self.torrent.go(torrent_id=id)
            torrent = self.page.get_torrent()
            return torrent
        except BrowserHTTPNotFound:
            return
