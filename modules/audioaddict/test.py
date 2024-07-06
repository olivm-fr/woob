# -*- coding: utf-8 -*-

# Copyright(C) 2013 Pierre Mazière
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
from woob.capabilities.radio import Radio
from woob.tools.value import Value


class AudioAddictTest(BackendTest):
    MODULE = 'audioaddict'

    def setUp(self):
        if not self.is_backend_configured():
            self.backend.config['networks'] = Value(value='RockRadio RadioTunes JazzRadio DI ClassicalRadio')
            self.backend.config['quality'] = Value(value='l')

    def test_audioaddict(self):
        ls = list(self.backend.iter_resources((Radio, ), []))
        self.assertTrue(len(ls) > 0)

        search = list(self.backend.iter_radios_search('classic'))
        self.assertTrue(len(search) > 0)

        radio = self.backend.get_radio('classicrock.RockRadio')
        self.assertTrue(radio.title)
        self.assertTrue(radio.description)
        self.assertTrue(radio.current.who)
        self.assertTrue(radio.current.what)
        self.assertTrue(radio.streams[0].url)
        self.assertTrue(radio.streams[0].title)
