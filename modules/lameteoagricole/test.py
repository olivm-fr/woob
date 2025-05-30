# Copyright(C) 2017      Vincent A
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

from datetime import date

from woob.capabilities.base import empty
from woob.tools.date import new_datetime
from woob.tools.test import BackendTest


class LameteoagricoleTest(BackendTest):
    MODULE = "lameteoagricole"

    def test_base(self):
        start = date.today()

        cities = list(self.backend.iter_city_search("paris"))
        assert cities

        for c in cities:
            assert c.id
            assert c.name

        c = cities[0]

        cur = self.backend.get_current(c.id)
        assert cur
        assert cur.temp
        assert cur.temp.unit
        assert not empty(cur.temp.value)
        assert cur.text

        forecast = list(self.backend.iter_forecast(c.id))
        assert forecast

        for f in forecast:
            assert f.date
            assert new_datetime(f.date) >= new_datetime(start)
            assert f.text
            assert -20 < f.low.value <= f.high.value < 40
            assert f.low.unit == "C"
            assert f.high.unit == "C"
