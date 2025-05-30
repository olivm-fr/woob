# Copyright(C) 2013 Pierre Mazière
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


from woob.tools.capabilities.streaminfo import StreamInfo

from .audio import BaseAudio, CapAudio
from .base import Field
from .file import CapFile


__all__ = ["BaseAudioStream", "CapAudioStream"]


class BaseAudioStream(BaseAudio):
    """
    Audio stream object
    """

    current = Field("Information related to current broadcast", StreamInfo)

    def __str__(self):
        return f"{self.title} ({self.url})"

    def __repr__(self):
        return f"{self.title!r} ({self.url!r})"


class CapAudioStream(CapAudio):
    """
    Audio streams provider
    """

    def search_audiostreams(self, pattern, sortby=CapFile.SEARCH_RELEVANCE):
        """
        Search an audio stream

        :param pattern: pattern to search
        :type pattern: str
        :param sortby: sort by ... (use SEARCH_* constants)
        :rtype: iter[:class:`BaseAudioStream`]
        """
        return self.search_audio(pattern, sortby)

    def get_audiostream(self, _id):
        """
        Get an audio stream

        :param _id: Audiostream ID
        :type id: str
        :rtype: :class:`BaseAudioStream`
        """
        return self.get_audio(_id)
