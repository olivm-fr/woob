# -*- coding: utf-8 -*-

# Copyright(C) 2010-2011 Romain Bignon
# Copyright(C) 2012 François Revol
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

from woob.capabilities.video import BaseVideo
from woob.tools.value import Value
from woob.tools.test import BackendTest
import itertools


class VimeoTest(BackendTest):
    MODULE = 'vimeo'

    def setUp(self):
        if not self.is_backend_configured():
            self.backend.config['quality'] = Value(value='2')
            self.backend.config['method'] = Value(value='progressive')

    def test_search(self):
        l = list(itertools.islice(self.backend.search_videos('boobs'), 0, 20))
        self.assertTrue(len(l) > 0)
        v = l[0]
        self.backend.fillobj(v, ('url',))
        self.assertTrue(v.url and v.url.startswith('https://'), 'URL for video "%s" not found: %s' % (v.id, v.url))

    def test_channels(self):
        l = list(itertools.islice(self.backend.iter_resources([BaseVideo], [u'vimeo-channels']), 0, 20))
        self.assertTrue(len(l) > 0)
        l1 = list(itertools.islice(self.backend.iter_resources([BaseVideo], l[0].split_path), 0, 20))
        self.assertTrue(len(l1) > 0)
        v = l1[0]
        self.backend.fillobj(v, ('url',))
        self.assertTrue(v.url and v.url.startswith('https://'), 'URL for video "%s" not found: %s' % (v.id, v.url))

    def test_categories(self):
        l = list(itertools.islice(self.backend.iter_resources([BaseVideo], [u'vimeo-categories']), 0, 20))
        self.assertTrue(len(l) > 0)
        l1 = list(itertools.islice(self.backend.iter_resources([BaseVideo], l[0].split_path), 0, 20))
        self.assertTrue(len(l1) > 0)
        v = l1[0]
        self.backend.fillobj(v, ('url',))
        self.assertTrue(v.url and v.url.startswith('https://'), 'URL for video "%s" not found: %s' % (v.id, v.url))
