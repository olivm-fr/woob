# Copyright(C) 2010-2021 Nicolas Duhamel
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

import re
from datetime import datetime

from woob.browser.pages import XMLPage
from woob.capabilities.base import NotAvailable, NotLoaded
from woob.capabilities.collection import Collection
from woob.capabilities.image import Thumbnail

from .video import CanalplusVideo


class ChannelsPage(XMLPage):
    ENCODING = "utf-8"

    def get_channels(self):
        """
        Extract all possible channels (paths) from the page
        """
        channels = list()
        for elem in self.doc[2]:
            for e in elem:
                if e.tag == "NOM":
                    fid, name = self._clean_name(e.text)
                    channels.append(Collection([fid], name))
                elif e.tag == "SELECTIONS":
                    for select in e:
                        sub_fid, subname = self._clean_name(select[1].text)
                        sub = Collection([fid, sub_fid], subname)
                        sub._link_id = select[0].text
                        channels.append(sub)
        return channels

    def _clean_name(self, name):
        name = name.strip()
        if name == name.upper():
            name = name.capitalize()
        friendly_id = re.sub(r"['/_ \(\)\-\+]+", "-", name).strip("-").lower()
        return friendly_id, name


class VideoPage(XMLPage):
    ENCODING = "utf-8"

    def parse_video(self, el, video=None):
        _id = el.find("ID").text
        if _id == "-1":
            # means the video is not found
            return None

        if not video:
            video = CanalplusVideo(_id)

        infos = el.find("INFOS")
        video.title = ""
        for part in infos.find("TITRAGE"):
            if len(part.text.strip()) == 0:
                continue
            if len(video.title) > 0:
                video.title += " — "
            video.title += part.text.strip()
        video.description = infos.find("DESCRIPTION").text

        media = el.find("MEDIA")
        url = media.find("IMAGES").find("PETIT").text
        if url:
            video.thumbnail = Thumbnail(url)
            video.thumbnail.url = video.thumbnail.id
        else:
            video.thumbnail = NotAvailable
        for format in media.find("VIDEOS"):
            if format.text is None:
                continue

            if format.tag == "HLS":
                video.ext = "m3u8"
                video.url = format.text
                break

        day, month, year = map(int, infos.find("PUBLICATION").find("DATE").text.split("/"))
        hour, minute, second = map(int, infos.find("PUBLICATION").find("HEURE").text.split(":"))
        video.date = datetime(year, month, day, hour, minute, second)

        return video

    def iter_results(self):
        for vid in self.doc.iter(tag="VIDEO"):
            video = self.parse_video(vid)
            video.url = NotLoaded
            yield video

    def iter_channel(self):
        for vid in self.doc.iter(tag="VIDEO"):
            yield self.parse_video_channel(vid)

    def parse_video_channel(self, el):
        _id = el[0].text
        video = CanalplusVideo(_id)
        video.title = "%s" % el[2][5][0].text
        video.date = datetime.now()
        return video

    def get_video(self, video):
        _id = self.params.get("id")
        for vid in self.doc.iter(tag="VIDEO"):
            if _id not in vid.find("ID").text:
                continue
            return self.parse_video(vid, video)
