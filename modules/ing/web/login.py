# -*- coding: utf-8 -*-

# Copyright(C) 2009-2014  Florent Fourcot, Romain Bignon
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import unicode_literals

from weboob.exceptions import ActionNeeded
from weboob.browser.pages import HTMLPage, LoggedPage
from weboob.browser.filters.standard import CleanText


class ActionNeededPage(HTMLPage):
    def on_load(self):
        if self.doc.xpath('//form//h1[1][contains(text(), "Accusé de reception du chéquier")]'):
            form = self.get_form(name='Alert')
            form['command'] = 'validateAlertMessage'
            form['radioValide_1_2_40003039944'] = 'Non'
            form.submit()
        elif self.doc.xpath('//p[@class="cddErrorMessage"]'):
            error_message = CleanText('//p[@class="cddErrorMessage"]')(self.doc)
            # TODO python2 handles unicode exceptions badly, fix when passing to python3
            raise ActionNeeded(error_message.encode('ascii', 'replace'))
        else:
            raise ActionNeeded(CleanText('//form//h1[1]')(self.doc))


class StopPage(HTMLPage):
    pass


class ReturnPage(LoggedPage, HTMLPage):
    def on_load(self):
        self.get_form(name='retoursso').submit()


class ApiRedirectionPage(LoggedPage, HTMLPage):
    def go_new_website(self):
        form = self.get_form(name="module")
        form.request.headers['Referer'] = "https://secure.ing.fr"
        form.submit()
