# Copyright(C) 2018      Vincent A
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


from woob.capabilities.contact import CapDirectory, Place
from woob.tools.backend import Module

from .browser import PagesjaunesBrowser


__all__ = ["PagesjaunesModule"]


class PagesjaunesModule(Module, CapDirectory):
    NAME = "pagesjaunes"
    DESCRIPTION = "Pages Jaunes"
    MAINTAINER = "Vincent A"
    EMAIL = "dev@indigo.re"
    LICENSE = "AGPLv3+"
    VERSION = "3.7"

    BROWSER = PagesjaunesBrowser

    def search_contacts(self, query, sortby):
        return self.browser.search_contacts(query)

    def fill_contact(self, obj, fields):
        if "opening" in fields:
            self.browser.fill_hours(obj)

    OBJECTS = {
        Place: fill_contact,
    }
