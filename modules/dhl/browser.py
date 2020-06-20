# -*- coding: utf-8 -*-

# Copyright(C) 2015      Matthieu Weber
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


from weboob.browser import PagesBrowser, URL

from .pages import DHLExpressSearchPage, DeutschePostDHLSearchPage


class DHLExpressBrowser(PagesBrowser):
    BASEURL = 'https://www.dhl.com'

    search_page = URL('/shipmentTracking\?AWB=(?P<id>.+)', DHLExpressSearchPage)

    def get_tracking_info(self, _id):
        return self.search_page.go(id=_id).get_info(_id)


class DeutschePostDHLBrowser(PagesBrowser):
    BASEURL = 'https://nolp.dhl.de'

    search_page = URL('/nextt-online-public/set_identcodes.do\?lang=en&idc=(?P<id>.+)', DeutschePostDHLSearchPage)

    def get_tracking_info(self, _id):
        return self.search_page.go(id=_id).get_info(_id)
