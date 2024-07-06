# -*- coding: utf-8 -*-

# Copyright(C) 2013  Fourcot Florent
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


class PoivyTest(BackendTest):
    MODULE = 'poivy'

    def test_list(self):
        """
        Test listing of subscriptions .
        No support of multi-account on the website, we could assume to
        have only one subscription.
        Check the balance if the subscription is ok.
        """
        subscriptions = list(self.backend.iter_subscription())
        self.assertTrue(len(subscriptions) == 1, msg="Account listing failed")
        self.assertTrue(self.backend.get_balance(subscriptions[0]) > 0,
                        msg="Get balance failed")

    def test_history(self):
        for subscription in self.backend.iter_subscription():
            self.assertTrue(len(list(self.backend.iter_documents_history(subscription))) > 0)
