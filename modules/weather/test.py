# Copyright(C) 2012 Arno Renevier
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


class WeatherTest(BackendTest):
    MODULE = "weather"

    def test_cities(self):
        paris = list(self.backend.iter_city_search("paris"))
        self.assertTrue(len(list(paris)) >= 1)

        current = self.backend.get_current(paris[0].id)
        self.assertTrue(current.temp.value is float(current.temp.value))

        forecasts = list(self.backend.iter_forecast(paris[0].id))
        self.assertTrue(len(forecasts) > 10)
