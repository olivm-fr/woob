# Copyright(C) 2016      Edouard Lambert
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from woob_modules.cmso.par.browser import CmsoParBrowser


class CmbParBrowser(CmsoParBrowser):
    BASEURL = 'https://api.cmb.fr'

    redirect_uri = 'https://mon.cmb.fr/auth/checkuser'
    error_uri = 'https://mon.cmb.fr/auth/errorauthn'
    client_uri = 'com.arkea.cmb.siteaccessible'

    name = 'cmb'
    arkea = '01'
    arkea_si = '001'
    arkea_client_id = 'ARCM6W0q6zHX31vvdVczlWRtGjSGbkPv'

    original_site = 'https://mon.cmb.fr'
