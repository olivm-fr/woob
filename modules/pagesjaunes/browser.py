# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import re

from weboob.browser import PagesBrowser, URL
from weboob.capabilities.contact import OpeningHours

from .pages import ResultsPage, PlacePage


class PagesjaunesBrowser(PagesBrowser):
    BASEURL = 'https://www.pagesjaunes.fr'

    search = URL(
        r'/annuaire/chercherlespros\?quoiqui=(?P<pattern>[a-z0-9-]+)&ou=(?P<city>[a-z0-9-]+)&page=(?P<page>\d+)',
        ResultsPage)
    company = URL(r'/pros/\d+', PlacePage)

    def simplify(self, name):
        return re.sub(r'[^a-z0-9-]+', '-', name.lower())

    def search_contacts(self, query):
        assert query.name
        assert query.city

        self.search.go(city=self.simplify(query.city), pattern=self.simplify(query.name), page=1)
        return self.page.iter_contacts()

    def fill_hours(self, contact):
        self.location(contact.url)
        contact.opening = OpeningHours()
        contact.opening.rules = list(self.page.iter_hours())
