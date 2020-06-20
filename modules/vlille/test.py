# -*- coding: utf-8 -*-

# Copyright(C) 2013      Bezleputh
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

from weboob.tools.test import BackendTest


class VlilleTest(BackendTest):
    MODULE = 'vlille'

    def test_vlille(self):
        _ = list(self.backend.iter_gauges())
        self.assertTrue(len(_) > 0)

        gauge = _[0]
        s = list(self.backend.iter_sensors(gauge))
        self.assertTrue(len(s) > 0)

        sensor = s[0]
        self.assertTrue(self.backend.get_last_measure(sensor.id) is not None)
