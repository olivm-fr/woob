# Copyright(C) 2010-2011 Romain Bignon
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
from woob.tools.test import BackendTest


class CanalPlusTest(BackendTest):
    MODULE = "canalplus"

    def test_canalplus(self):
        l = list(self.backend.search_videos("guignol"))
        self.assertTrue(len(l) > 0)
        v = l[0]
        self.backend.fillobj(v, ("url",))
        self.assertTrue(
            v.url and (v.url.startswith("rtmp://") or v.url.startswith("http://")),
            f'URL for video "{v.id}" not found: {v.url}',
        )

    def test_ls(self):
        l = list(self.backend.iter_resources((BaseVideo,), []))
        self.assertTrue(len(l) > 0)

        l = list(self.backend.iter_resources((BaseVideo,), ["sport"]))
        self.assertTrue(len(l) > 0)
