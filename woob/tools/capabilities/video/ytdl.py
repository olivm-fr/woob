# Copyright(C) 2017  Vincent A
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

import subprocess
from datetime import timedelta
from shutil import which

from woob.capabilities.base import NotAvailable
from woob.capabilities.image import Thumbnail
from woob.capabilities.video import BaseVideo
from woob.tools.date import parse_date
from woob.tools.json import json


__all__ = ("video_info",)


def video_info(url):
    """Fetch info about a video using youtube-dl

    :param url: URL of the web page containing the video
    :rtype: :class:`woob.capabilities.video.Video`
    """

    ytdl = which("yt-dlp") or which("youtube-dl")
    if ytdl is None:
        raise Exception("Please install youtube-dl")

    try:
        j = json.loads(subprocess.check_output([ytdl, "-J", url]))
    except subprocess.CalledProcessError:
        return None

    v = BaseVideo(id=url)
    v.title = str(j.get("title")) if j.get("title") else NotAvailable
    v.ext = str(j.get("ext")) if j.get("ext") else NotAvailable
    v.description = str(j.get("description")) if j.get("description") else NotAvailable
    v.url = str(j["url"])
    v.duration = timedelta(seconds=j.get("duration")) if j.get("duration") else NotAvailable
    v.author = str(j.get("uploader")) if j.get("uploader") else NotAvailable
    v.rating = j.get("average_rating") or NotAvailable

    if j.get("thumbnail"):
        v.thumbnail = Thumbnail(str(j["thumbnail"]))

    d = j.get("upload_date", j.get("release_date"))
    if d:
        v.date = parse_date(d)

    return v
