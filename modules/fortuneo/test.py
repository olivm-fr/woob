# Copyright(C) 2012 Gilles-Alexandre Quenot
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


from woob.tools.test import BackendTest


class FortuneoTest(BackendTest):
    MODULE = "fortuneo"

    def test_fortuneo(self):
        l = list(self.backend.iter_accounts())
        self.assertTrue(len(l) > 0)
        a = l[0]
        list(self.backend.iter_history(a))


# vim:ts=4:sw=4
