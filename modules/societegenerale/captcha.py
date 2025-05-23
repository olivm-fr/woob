# Copyright(C) 2010-2011 Jocelyn Jaubert
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

import hashlib

from PIL import Image

from woob.tools.log import getLogger


class TileError(Exception):
    def __init__(self, msg, tile=None):
        super().__init__(msg)
        self.tile = tile


class Captcha:
    # vk_visuel=swm_ngim : 240 x 240
    # vk_visuel= : 96 x 92
    def __init__(self, file, infos):
        self.inim = Image.open(file)
        self.infos = infos
        self.nbr = int(infos["nbrows"])
        self.nbc = int(infos["nbcols"])
        (self.nx, self.ny) = self.inim.size
        self.width = self.nx // self.nbr
        self.height = self.ny // self.nbc
        self.inmat = self.inim.load()
        self.map = {}

        self.tiles = [[Tile(y * self.nbc + x) for y in range(4)] for x in range(4)]

    def __getitem__(self, coords):
        x, y = coords
        return self.inmat[x % self.nx, y % self.ny]

    def all_coords(self):
        for y in range(self.ny):
            for x in range(self.nx):
                yield x, y

    def get_codes(self, code):
        s = ""
        num = 0
        for c in code:
            index = self.map[int(c)].id
            keycode = str(self.infos["grid"][num * self.nbr * self.nbc + index])
            s += keycode
            if num < 5:
                s += ","
            num += 1
        return s

    def build_tiles(self):
        for ty in range(0, self.nbc):
            y = ty * self.height

            for tx in range(0, self.nbr):
                x = tx * self.width

                tile = self.tiles[tx][ty]

                for yy in range(y, y + self.height):
                    for xx in range(x, x + self.width):
                        tile.map.append(self[xx, yy])

                num = tile.get_num()
                if num > -1:
                    tile.valid = True
                    self.map[num] = tile


class Tile:
    hash = {
        "ff1441b2c5f90703ef04e688e399aca5": 1,
        "53d7f3dfd64f54723b231fc398b6be57": 2,
        "5bcba7fa2107ba9a606e8d0131c162eb": 3,
        "9db6e7ed063e5f9a69ab831e6cc0d721": 4,
        "30ebb75bfa5077f41ccfb72e8c9cc15b": 5,
        "61e27275e494038e524bc9fbbd0be130": 6,
        "0e0816f1b743f320ca561f090df0fbb1": 7,
        "11e7d4a6d447e66a5a112c1d9f7fc442": 8,
        "2ea3c82768030d91571d360acf7a0f75": 9,
        "28a834ebbf0238b46d3fffae1a0b781b": 0,
        "04211db029ce488e07010f618a589c71": -1,
        #
        "9a1bdf493d4067e98d3f364586c81e9d": 1,
        "932032493860463bb4a3df7c99a900ad": 2,
        "59cd90f1fa0b416ecdb440bc16d0b8e7": 3,
        "53fe822c5efebe5f6fdef0f272c29638": 4,
        "2082a9c830c0c7c9c22e9c809c6cadf7": 5,
        "7f24aa97f0037bddcf2a4c8c2dbf5948": 6,
        "725b6f11f44ecc2e9f6e79e86e3a82a5": 7,
        "61d57da23894b96fab11f7b83c055bba": 8,
        "18f6290c1cfaecadc5992e7ef6047a49": 9,
        "1ce77709ec1d7475685d7b50d6f1c89e": 0,
        "6718858a509fff4b86604f3096cf65e1": -1,
        #
        "e7438dc8d0b7db73a9611c2880700d23": 1,
        "111d88d6ea8671a7ca2982e08558743b": 2,
        "8d37303d0a23833cacd79d5f2ec1c4fd": 3,
        "1e7895d6095d303871482a1a05a01d68": 4,
        "894e1db1b7a6d7b0d7ee17d815a4ca73": 5,
        "1ee5c879d3e26387560188538d473d18": 6,
        "d13e79f72e7a33d4f83066a9676c5ded": 7,
        "7761a4b85d7034fff4162222803fde08": 8,
        "ebd8c4e5f1f125dd2f60bf8f3f223c3d": 9,
        "c642a9840cb202da659eb316a369a4a5": 0,
        "e9188c3211d64b4bdfd0cae8e4354f65": -1,
    }

    def __init__(self, _id):
        self.id = _id
        self.valid = False
        self.logger = getLogger("societegenerale.captcha")
        self.map = []

    def __repr__(self):
        return "<Tile(%02d) valid=%s>" % (self.id, self.valid)

    def checksum(self):
        s = ""
        for pxls in self.map:
            for pxl in pxls:
                s += "%02d" % pxl
        return hashlib.md5(s.encode("ascii")).hexdigest()

    def get_num(self):
        sum = self.checksum()
        try:
            return self.hash[sum]
        except KeyError:
            self.display()
            raise TileError("Tile not found " + sum, self)

    def display(self):
        self.logger.debug(self.checksum())
        # im = Image.new('RGB', (24, 23))
        # im.putdata(self.map)
        # im.save('/tmp/%s.png' % self.checksum())
