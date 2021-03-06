# -*- coding: utf-8 -*-

# Copyright(C) 2016      Edouard Lambert
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from weboob.browser import AbstractBrowser


class CmbParBrowser(AbstractBrowser):
    PARENT = 'cmso'
    PARENT_ATTR = 'package.par.browser.CmsoParBrowser'
    BASEURL = 'https://api.cmb.fr'

    redirect_uri = 'https://mon.cmb.fr/auth/checkuser'
    error_uri = 'https://mon.cmb.fr/auth/errorauthn'
    client_uri = 'com.arkea.cmb.siteaccessible'

    name = 'cmb'
    arkea = '01'
    arkea_si = '001'
    arkea_client_id = 'ARCM6W0q6zHX31vvdVczlWRtGjSGbkPv'

    original_site = 'https://mon.cmb.fr'
