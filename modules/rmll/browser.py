# Copyright(C) 2015 Guilhem Bonnefille
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
from woob.browser.exceptions import HTTPNotFound

from .pages import RmllChannelsPage, RmllCollectionPage, RmllDurationPage, RmllLatestPage, RmllSearchPage, RmllVideoPage


__all__ = ["RmllBrowser"]


class RmllBrowser(PagesBrowser):
    BASEURL = "https://rmll.ubicast.tv"

    index_page = URL(r"channels/content/(?P<id>.+)", RmllCollectionPage)
    latest_page = URL(r"api/v2/latest/", RmllLatestPage)
    video_page = URL(r"permalink/(?P<id>.+)/", RmllVideoPage)
    channels_page = URL(r"api/v2/channels/content/\?parent_oid=(?P<oid>.*)", RmllChannelsPage)
    search_page = URL(r"api/v2/search/\?search=(?P<pattern>.+)", RmllSearchPage)
    duration_page = URL(r"api/v2/medias/modes/\?oid=(?P<oid>.*)", RmllDurationPage)

    def __init__(self, *args, **kwargs):
        self.channels = None
        PagesBrowser.__init__(self, *args, **kwargs)

    @video_page.id2url
    def get_video(self, url, video=None):
        self.location(url)
        assert self.video_page.is_here()
        video = self.page.get_video(obj=video)
        video.duration = self.duration_page.go(oid=video.id).get_duration()
        return video

    def search_videos(self, pattern):
        url = self.search_page.build(pattern=pattern)
        self.location(url)
        return self.page.iter_resources()

    def get_latest_videos(self):
        url = self.latest_page.build()
        self.location(url)
        assert self.latest_page.is_here()
        return self.page.iter_resources()

    def get_channel_videos(self, split_path):
        oid = ""
        if len(split_path) > 0:
            oid = split_path[-1]
        try:
            url = self.channels_page.build(oid=oid)
            self.location(url)
            assert self.channels_page.is_here()
            yield from self.page.iter_resources(split_path)

        except HTTPNotFound:
            pass
