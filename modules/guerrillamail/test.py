# -*- coding: utf-8 -*-

# Copyright(C) 2013      Vincent A
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
from uuid import uuid4


class GuerrillamailTest(BackendTest):
    MODULE = 'guerrillamail'

    def test_guerrillamail(self):
        box = uuid4()
        thread = self.backend.get_thread(box)
        self.assertTrue(thread)
        message = thread.root
        self.assertTrue(message)
        self.assertTrue(message.sender)
        self.assertTrue(message.title)
        self.assertTrue(message.date)
        self.assertTrue(message.receivers)
