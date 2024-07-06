# -*- coding: utf-8 -*-

# Copyright(C) 2017-2021 Romain Bignon
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


from woob.capabilities.base import NotAvailable
from woob.capabilities.paste import PasteNotFound
from woob.tools.test import BackendTest


class SprungeTest(BackendTest):
    MODULE = 'sprunge'

    def _get_paste(self, _id):
        # html method
        p = self.backend.get_paste(_id)
        self.backend.fillobj(p, ['title'])
        assert p.title is NotAvailable
        assert p.page_url.startswith('http://sprunge.us/')
        assert p.public is False

    def test_post(self):
        p = self.backend.new_paste(None, contents=u'Woob Test héhéhé')
        self.backend.post_paste(p, max_age=False)
        assert p.id
        self.backend.fill_paste(p, ['title'])
        assert p.title is NotAvailable
        assert p.id in p.page_url
        assert p.public is False

        # test all get methods from the Paste we just created
        self._get_paste(p.id)

        # same but from the full URL
        self._get_paste('http://sprunge.us/%s' % p.id)

    def test_notfound(self):
        for _id in ('ab',
                    'http://sprunge.us/ab'):
            # html method
            p = self.backend.get_paste(_id)
            self.assertRaises(PasteNotFound, self.backend.fillobj, p, ['title'])

            # raw method
            p = self.backend.get_paste(_id)
            self.assertRaises(PasteNotFound, self.backend.fillobj, p, ['contents'])

    def test_checkurl(self):
        # call with an URL we can't handle with this backend
        assert self.backend.get_paste('http://pastebin.com/nJG9ZFG8') is None

    def test_can_post(self):
        assert 2 == self.backend.can_post(u'hello', public=False)
        assert 1 == self.backend.can_post(u'hello', public=False, title=u'hello')
        assert 1 == self.backend.can_post(u'hello', title=u'hello')
        assert 0 == self.backend.can_post(u'hello', public=True)
        assert 0 == self.backend.can_post(u'hello', public=False, max_age=3600*24)
