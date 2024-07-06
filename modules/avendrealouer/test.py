# -*- coding: utf-8 -*-

# Copyright(C) 2017      ZeHiro
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
from woob.tools.capabilities.housing.housing_test import HousingTest
from woob.capabilities.housing import Query, POSTS_TYPES, HOUSE_TYPES


class AvendrealouerTest(BackendTest, HousingTest):
    MODULE = 'avendrealouer'

    def test_avendre_rent(self):
        # la rechercher de bien en location ne distingue pas meublé ou non
        self.DO_NOT_DISTINGUISH_FURNISHED_RENT = True
        query = Query()
        query.area_min = 20
        query.cost_max = 1500
        query.type = POSTS_TYPES.RENT
        query.cities = []
        for city in self.backend.search_city('paris'):
            city.backend = self.backend.name
            query.cities.append(city)
        self.check_against_query(query)

    def test_foncia_furnished_rent(self):
        # Dans ce cas, on veut vraiment vérifier si on est sur du meublé
        self.DO_NOT_DISTINGUISH_FURNISHED_RENT = False
        query = Query()
        query.area_min = 20
        query.cost_max = 1500
        query.type = POSTS_TYPES.FURNISHED_RENT
        query.house_types = [HOUSE_TYPES.APART]
        query.cities = []
        for city in self.backend.search_city('paris'):
            city.backend = self.backend.name
            query.cities.append(city)
        self.check_against_query(query)

    def test_avendre_sale(self):
        query = Query()
        query.area_min = 20
        query.type = POSTS_TYPES.SALE
        query.cities = []
        for city in self.backend.search_city('paris'):
            city.backend = self.backend.name
            query.cities.append(city)
        self.check_against_query(query)

    def test_avendre_viager(self):
        query = Query()
        query.type = POSTS_TYPES.VIAGER
        query.cities = []
        for city in self.backend.search_city('paris'):
            city.backend = self.backend.name
            query.cities.append(city)
        self.check_against_query(query)
