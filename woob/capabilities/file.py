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

from .base import BaseObject, Capability, Enum, Field, IntField, NotAvailable, StringField
from .date import DateField


__all__ = ["BaseFile", "CapFile"]


class Licenses(Enum):
    OTHER = "Other license"
    PD = "Public Domain"
    COPYRIGHT = "All rights reserved"
    CCBY = "Creative Commons BY"
    CCBYSA = "Creative Commons BY-SA"
    CCBYNC = "Creative Commons BY-NC"
    CCBYND = "Creative Commons BY-ND"
    CCBYNCSA = "Creative Commons BY-NC-SA"
    CCBYNCND = "Creative Commons BY-NC-ND"
    GFDL = "GNU Free Documentation License"


LICENSES = Licenses


class BaseFile(BaseObject):
    """
    Represent a file.
    """

    title = StringField("File title")
    ext = StringField("File extension")
    mime_type = StringField("MIME Type")
    author = StringField("File author")
    description = StringField("File description")
    date = DateField("File publication date")
    last_update = DateField("Last update or last modified date", default=NotAvailable)
    size = IntField("File size in bytes", default=NotAvailable)
    rating = Field("Rating", int, float, default=NotAvailable)
    rating_max = Field("Maximum rating", int, float, default=NotAvailable)
    license = StringField("License name")

    def __str__(self):
        return self.url or ""

    def __repr__(self):
        return f"<{type(self).__name__} title={self.title!r} url={self.url!r}>"

    @classmethod
    def id2url(cls, _id):
        """
        Overloaded in child classes provided by backends.
        """
        raise NotImplementedError()

    @property
    def page_url(self):
        """
        Get file page URL
        """
        return self.id2url(self.id)


class SearchSort(Enum):
    RELEVANCE = 0
    RATING = 1
    VIEWS = 2
    DATE = 3


class CapFile(Capability):
    """
    Provide file download
    """

    SEARCH_RELEVANCE = SearchSort.RELEVANCE
    SEARCH_RATING = SearchSort.RATING
    SEARCH_VIEWS = SearchSort.VIEWS
    SEARCH_DATE = SearchSort.DATE

    def search_file(self, pattern, sortby=SEARCH_RELEVANCE):
        """
        :param pattern: pattern to search on
        :type pattern: str
        :param sortby: sort by ... (user SEARCH_* constants)
        :rtype: iter[:class:`BaseFile`]
        """
        raise NotImplementedError()

    def get_file(self, _id):
        """
        Get a file from an ID

        :param _id: the file id. I can be a numeric ID, or a page url
        :type _id: str
        :rtype: :class:`BaseFile` or None if not found.
        """
        raise NotImplementedError()
