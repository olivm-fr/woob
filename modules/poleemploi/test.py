# Copyright(C) 2013      Bezleputh
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

from woob.tools.test import BackendTest
from woob.tools.value import Value


class PoleEmploiTest(BackendTest):
    MODULE = "poleemploi"

    def setUp(self):
        if not self.is_backend_configured():
            self.backend.config["metier"] = Value(value="informaticien")
            self.backend.config["place"] = Value(value="100|PAYS|01")
            self.backend.config["salary"] = Value(value="15000")
            self.backend.config["qualification"] = Value(value="9")
            self.backend.config["domain"] = Value(value="M18")
            self.backend.config["limit_data"] = Value(value="93")

    def test_poleemploi_search(self):
        l = list(self.backend.search_job("infographiste"))
        assert len(l)
        advert = self.backend.get_job_advert(l[0].id, l[0])
        self.assertTrue(advert.url, f'URL for announce "{advert.id}" not found: {advert.url}')

    def test_poleemploi_advanced_search(self):
        l = list(self.backend.advanced_search_job())
        assert len(l)
        advert = self.backend.get_job_advert(l[0].id, l[0])
        self.assertTrue(advert.url, f'URL for announce "{advert.id}" not found: {advert.url}')
