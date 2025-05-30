# Copyright(C) 2016      Vincent A
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

import dateutil.parser

from woob.browser import URL
from woob.browser.browsers import APIBrowser
from woob.capabilities.gallery import CapGallery


class ImgurBrowser(APIBrowser):
    BASEURL = "https://api.imgur.com"

    CLIENT_ID = "87a8e692cb09382"

    SORT_TYPE = {
        CapGallery.SEARCH_DATE: "time",
        CapGallery.SEARCH_VIEWS: "viral",
        CapGallery.SEARCH_RATING: "top",
        CapGallery.SEARCH_RELEVANCE: "top",
    }

    search_url = URL(r"/3/gallery/search/(?P<sort_type>\w+)/(?P<page>\d+)/\?q=(?P<pattern>.*)")
    get_gallery_url = URL(r"/3/album/(?P<id>\w+)")
    get_image_url = URL(r"/3/image/(?P<id>\w+)")

    def open_raw(self, *args, **kwargs):
        return super().open(*args, **kwargs)

    def fill_file(self, file, fields):
        response = self.open_raw(file.url)
        if "date" in fields:
            file.date = dateutil.parser.parse(response.headers.get("Date"))
        if "data" in fields:
            file.data = response.content
        if "size" in fields:
            file.size = len(response.content)

    def open(self, *args, **kwargs):
        kwargs.setdefault("headers", {})
        kwargs["headers"]["Authorization"] = "Client-ID %s" % self.CLIENT_ID
        return super().open(*args, **kwargs)

    def request(self, *args, **kwargs):
        reply = super().request(*args, **kwargs)
        if reply["success"]:
            return reply["data"]

    def post_image(self, b64, title=""):
        res = {}
        params = {"image": b64, "title": title or "", "type": "base64"}
        info = self.request("https://api.imgur.com/3/image", data=params)
        if info is not None:
            res["id"] = info["id"]
            res["delete_url"] = "https://api.imgur.com/3/image/%s" % info["deletehash"]
            return res

    def get_image(self, id):
        url = self.get_image_url.build(browser=self, id=id)
        return self.request(url)

    def get_gallery(self, id):
        url = self.get_gallery_url.build(browser=self, id=id)
        return self.request(url)

    def search_items(self, pattern, sortby):
        sortby = self.SORT_TYPE[sortby]
        url = self.search_url.build(browser=self, sort_type=sortby, page=1, pattern=pattern)
        info = self.request(url)
        if info is None:
            return []
        return info
