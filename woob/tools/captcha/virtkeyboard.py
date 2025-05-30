# Copyright(C) 2011  Pierre Mazière
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import hashlib
import tempfile
from typing import IO, TYPE_CHECKING, ClassVar


if TYPE_CHECKING:
    from woob.browser import Browser


try:
    from PIL import Image
except ImportError:
    raise ImportError("Please install python-imaging")


class VirtKeyboardError(Exception):
    pass


class VirtKeyboard:
    """
    Handle a virtual keyboard.
    """

    margin = None
    """
    Margin used by :meth:`get_symbol_coords` to reduce size
    of each "key" of the virtual keyboard. This attribute is always
    converted to a 4-tuple, and has the same semantic as the CSS
    ``margin`` property (top, right, bottom, right), in pixels.
    """

    codesep = ""
    """
    Output separator between code strings.

    See :func:`get_string_code`.
    """

    def __init__(self, file=None, coords=None, color=None, convert=None):
        # file: virtual keyboard image
        # coords: dictionary <value to return>:<tuple(x1,y1,x2,y2)>
        # color: color of the symbols in the image
        #        depending on the image, it can be a single value or a tuple
        # convert: if not None, convert image to this target type (for example 'RGB')

        if file is not None:
            assert color, "No color provided !"
            self.load_image(file, color, convert)

        if type(self.margin) in (int, float):
            self.margin = (self.margin,) * 4
        elif self.margin is not None:
            if len(self.margin) == 2:
                self.margin = self.margin + self.margin
            elif len(self.margin) == 3:
                self.margin = self.margin + (self.margin[1],)
            assert len(self.margin) == 4

        if coords is not None:
            self.load_symbols(coords)

    def load_image(self, file, color, convert=None):
        self.image = Image.open(file)

        if convert is not None:
            self.image = self.image.convert(convert)

        self.bands = self.image.getbands()
        if isinstance(color, int) and not isinstance(self.bands, str) and len(self.bands) != 1:
            raise VirtKeyboardError("Color requires %i component but only 1 is provided" % len(self.bands))
        if not isinstance(color, int) and len(color) != len(self.bands):
            raise VirtKeyboardError("Color requires %i components but %i are provided" % (len(self.bands), len(color)))
        self.color = color

        self.width, self.height = self.image.size
        self.pixar = self.image.load()

    def load_symbols(self, coords):
        self.coords = {}
        self.md5 = {}
        for i in coords:
            coord = self.get_symbol_coords(coords[i])
            if coord == (-1, -1, -1, -1):
                continue
            self.coords[i] = coord
            self.md5[i] = self.checksum(self.coords[i])

    def check_color(self, pixel):
        return pixel == self.color

    def get_symbol_coords(self, coords):
        """Return narrow coordinates around symbol."""
        (x1, y1, x2, y2) = coords
        if self.margin:
            top, right, bottom, left = self.margin
            x1, y1, x2, y2 = x1 + left, y1 + top, x2 - right, y2 - bottom

        newY1 = -1
        newY2 = -1
        for y in range(y1, min(y2 + 1, self.height)):
            empty_line = True
            for x in range(x1, min(x2 + 1, self.width)):
                if self.check_color(self.pixar[x, y]):
                    empty_line = False
                    if newY1 < 0:
                        newY1 = y
                    break
            if newY1 >= 0 and not empty_line:
                newY2 = y
        newX1 = -1
        newX2 = -1
        for x in range(x1, min(x2 + 1, self.width)):
            empty_column = True
            for y in range(y1, min(y2 + 1, self.height)):
                if self.check_color(self.pixar[x, y]):
                    empty_column = False
                    if newX1 < 0:
                        newX1 = x
                    break
            if newX1 >= 0 and not empty_column:
                newX2 = x
        return (newX1, newY1, newX2, newY2)

    def checksum(self, coords):
        (x1, y1, x2, y2) = coords
        s = b""
        for y in range(y1, min(y2 + 1, self.height)):
            for x in range(x1, min(x2 + 1, self.width)):
                if self.check_color(self.pixar[x, y]):
                    s += b"."
                else:
                    s += b" "
        return hashlib.md5(s).hexdigest()

    def get_symbol_code(self, all_known_md5_for_symbol):
        if isinstance(all_known_md5_for_symbol, str):
            all_known_md5_for_symbol = [all_known_md5_for_symbol]

        current_md5_in_keyboard = self.md5

        for known_md5 in all_known_md5_for_symbol:
            for code, cur_md5 in current_md5_in_keyboard.items():
                if known_md5 == cur_md5:
                    return code
        raise VirtKeyboardError('Code not found for these hashes "%s".' % all_known_md5_for_symbol)

    def get_string_code(self, string):
        return self.codesep.join(self.get_symbol_code(self.symbols[c]) for c in string)

    def check_symbols(self, symbols, dirname):
        # symbols: dictionary <symbol>:<md5 value>
        for s in symbols:
            try:
                self.get_symbol_code(symbols[s])
            except VirtKeyboardError:
                if dirname is None:
                    dirname = tempfile.mkdtemp(prefix="woob_session_")
                self.generate_MD5(dirname)
                raise VirtKeyboardError(f"Symbol '{s}' not found; all symbol hashes are available in {dirname}")

    def generate_MD5(self, dir):
        for i in self.coords:
            width = self.coords[i][2] - self.coords[i][0] + 1
            height = self.coords[i][3] - self.coords[i][1] + 1
            img = Image.new("".join(self.bands), (width, height))
            matrix = img.load()
            for y in range(height):
                for x in range(width):
                    matrix[x, y] = self.pixar[self.coords[i][0] + x, self.coords[i][1] + y]
            img.save(dir + "/" + self.md5[i] + ".png")
        self.image.save(dir + "/image.png")


class MappedVirtKeyboard(VirtKeyboard):
    def __init__(self, file, document, img_element, color, map_attr="onclick", convert=None):
        map_id = img_element.attrib.get("usemap")[1:]
        map = document.find('//map[@id="%s"]' % map_id)
        if map is None:
            map = document.find('//map[@name="%s"]' % map_id)

        coords = {}
        for area in map.iter("area"):
            code = area.attrib.get(map_attr)
            area_coords = []
            for coord in area.attrib.get("coords").split(" ")[0].split(","):
                area_coords.append(int(coord))
            coords[code] = tuple(area_coords)

        super().__init__(file, coords, color, convert)


class GridVirtKeyboard(VirtKeyboard):
    """
    Make a virtual keyboard where "keys" are distributed on a grid.
    Example here: https://www.e-sgbl.com/portalserver/sgbl-web/login

    :param symbols: Sequence of symbols, ordered in the grid from left to
        right and up to down
    :type symbols: iterable
    :param cols: Column count of the grid
    :type cols: int
    :param rows: Row count of the grid
    :type rows: int
    :param image: File-like object to be used as data source
    :type image: file
    :param color: Color of the meaningful pixels
    :type color: 3-tuple
    :param convert: Mode to which convert color of pixels, see
        :meth:`Image.Image.convert` for more information
    """

    symbols = {}
    """Assocation table between symbols and md5s"""

    def __init__(self, symbols, cols, rows, image, color, convert=None):
        self.load_image(image, color, convert)

        tileW = self.width / cols
        tileH = self.height / rows
        positions = ((s, i * tileW % self.width, i // cols * tileH) for i, s in enumerate(symbols))
        coords = {s: tuple(map(int, (x, y, x + tileW, y + tileH))) for (s, x, y) in positions}

        super().__init__()

        self.load_symbols(coords)


class SplitKeyboard:
    """Virtual keyboard for when the chars are in individual images, not a single grid"""

    char_to_hash = None
    """dict mapping password characters to image hashes"""

    codesep = ""
    """Output separator between symbols"""

    def __init__(self, code_to_filedata):
        """Create a SplitKeyboard

        :param code_to_filedata: dict mapping site codes to images data
        :type code_to_filedata: dict[str, str]
        """

        hash_to_code = {self.checksum(data): code for code, data in code_to_filedata.items()}

        self.char_to_code = {}
        for char, hashes in self.char_to_hash.items():
            if isinstance(hashes, str):
                hashes = (hashes,)

            for hash in hash_to_code:
                if hash in hashes:
                    self.char_to_code[char] = hash_to_code.pop(hash)
                    break
            else:
                path = tempfile.mkdtemp(prefix="woob_session_")
                self.dump(code_to_filedata.values(), path)
                raise VirtKeyboardError(f"Symbol '{char}' not found; all symbol hashes are available in {path}")

    def checksum(self, buffer):
        return hashlib.md5(self.convert(buffer)).hexdigest()

    def dump(self, files, path):
        for dat in files:
            md5 = hashlib.md5(dat).hexdigest()
            with open(f"{path}/{md5}.png", "wb") as fd:
                fd.write(dat)

    def get_string_code(self, password):
        symbols = []
        for c in password:
            symbols.append(self.char_to_code[c])
        return self.codesep.join(symbols)

    def convert(self, buffer):
        return buffer

    @classmethod
    def create_from_url(cls, browser, code_to_url):
        code_to_file = {code: browser.open(url).content for code, url in code_to_url}
        return cls(code_to_file)


class Tile:
    """Tile of a image grid for SimpleVirtualKeyboard"""

    def __init__(self, matching_symbol, coords, image=None, md5=None):
        self.matching_symbol = matching_symbol
        self.coords = coords
        self.image = image
        self.md5 = md5


class SimpleVirtualKeyboard:
    """Handle a virtual keyboard where "keys" are distributed on a simple grid.

    :param cols: Column count of the grid
    :param rows: Row count of the grid
    :param file: File-like object to be used as data source
    :param convert: Mode to which convert color of pixels, see
        :meth:`Image.Image.convert` for more information
    :param matching_symbols: symbol that match all case of image grid from left to right and top
        to down, European reading way.
    :param matching_symbols_coords: dict mapping matching website symbols to their image coords
        (x0, y0, x1, y1) on grid image from left to right and top to
        down, European reading way. It's not symbols in the image.
    :param browser: Browser of woob session.
        Allow to dump tiles files in same directory than session folder
    """

    codesep: ClassVar[str] = ""
    """Output separator between matching symbols"""

    margin: ClassVar[tuple[int, int, int, int] | tuple[int, int] | int | None] = None
    """
    4-tuple(int), same as HTML margin: (top, right, bottom, left).
    or 2-tuple(int), (top = bottom, right = left),
    or int, top = right = bottom = left
    """

    tile_margin: ClassVar[tuple[int, int, int, int] | tuple[int, int] | int | None] = None
    """
    4-tuple(int), same as HTML margin: (top, right, bottom, left).
    or 2-tuple(int), (top = bottom, right = left),
    or int, top = right = bottom = left
    """

    symbols: ClassVar[dict[str, str | tuple[str, ...]]] = None
    """
    Association table between image symbols and md5s
    """

    convert: ClassVar[str | None] = None
    """
    Mode to which convert color of pixels, see
    :meth:`Image.Image.convert` for more information
    """

    tile_klass = Tile

    def __init__(
        self,
        file: IO,
        cols: int,
        rows: int,
        matching_symbols: list[str] | None = None,
        matching_symbols_coords: dict[str, tuple[int, int, int, int]] | None = None,
        browser: Browser | None = None,
    ):
        self.cols = cols
        self.rows = rows

        # Needed even if init is overwrite
        self.path = self.build_path(browser)

        # Get self.image
        self.load_image(file, self.margin, self.convert)

        # Get self.tiles
        self.get_tiles(matching_symbols=matching_symbols, matching_symbols_coords=matching_symbols_coords)

        # Tiles processing
        self.cut_tiles(self.tile_margin)
        self.hash_md5_tiles()

    def build_path(self, browser=None):
        if browser and browser.responses_dirname:
            return browser.responses_dirname
        else:
            return tempfile.mkdtemp(prefix="woob_session_")

    def load_image(self, file, margin=None, convert=None):
        self.image = Image.open(file)
        # Resize image if margin is given
        if margin:
            self.image = self.cut_margin(self.image, margin)
        if convert:
            self.image = self.image.convert(convert)
        # Give possibility to alter image before get tiles, overwrite :func:`alter_image`.
        self.alter_image()
        self.width, self.height = self.image.size

    def alter_image(self):
        pass

    def cut_margin(self, image, margin):
        width, height = image.size

        # Verify the magin value format
        if type(margin) is int:
            margin = (margin, margin, margin, margin)
        elif len(margin) == 2:
            margin = (margin[0], margin[1], margin[0], margin[1])
        elif len(margin) == 4:
            margin = margin
        else:
            assert (len(margin) == 3) & (len(margin) > 4), "Margin format is wrong."

        assert ((margin[0] + margin[2]) < height) & (
            (margin[1] + margin[3]) < width
        ), "Margin is too high, there is not enough pixel to cut."

        image = image.crop((0 + margin[3], 0 + margin[0], width - margin[1], height - margin[2]))
        return image

    def get_tiles(self, matching_symbols=None, matching_symbols_coords=None):
        self.tiles = []

        # Tiles coords are given
        if matching_symbols_coords:
            for matching_symbol in matching_symbols_coords:
                self.tiles.append(
                    self.tile_klass(matching_symbol=matching_symbol, coords=matching_symbols_coords[matching_symbol])
                )
            return

        assert (not self.width % self.cols) & (
            not self.height % self.rows
        ), "Image width and height are not multiple of cols and rows. Please resize image with attribute `margin`."

        # Tiles coords aren't given, calculate them
        self.tileW = self.width // self.cols
        self.tileH = self.height // self.rows

        # Matching symbols aren't given, default value is range(columns*rows)
        if not matching_symbols:
            matching_symbols = ["%s" % i for i in range(self.cols * self.rows)]

        assert len(matching_symbols) == (
            self.cols * self.rows
        ), "Number of website matching symbols is not equal to the number of cases on the image."

        # Calculate tiles coords for each matching symbol from 1-dimension to 2-dimensions
        for index, matching_symbol in enumerate(matching_symbols):
            coords = self.get_tile_coords_in_grid(index)
            self.tiles.append(self.tile_klass(matching_symbol=matching_symbol, coords=coords))

    def get_tile_coords_in_grid(self, case_index):
        # Get the top left pixel coords of the tile
        x0 = (case_index % self.cols) * self.tileW
        y0 = (case_index // self.cols) * self.tileH

        # Get the bottom right coords of the tile
        x1 = x0 + self.tileW
        y1 = y0 + self.tileH

        coords = (x0, y0, x1, y1)
        return coords

    def cut_tiles(self, tile_margin=None):
        for tile in self.tiles:
            tile.image = self.image.crop(tile.coords)

        # Resize tile if margin is given
        if tile_margin:
            for tile in self.tiles:
                tile.image = self.cut_margin(tile.image, tile_margin)

    def hash_md5_tiles(self):
        for tile in self.tiles:
            tile.md5 = hashlib.md5(tile.image.tobytes()).hexdigest()

    def dump_tiles(self, path):
        for tile in self.tiles:
            tile.image.save(f"{path}/{tile.md5}.png")

    def get_string_code(self, password):
        word = []

        for digit in password:
            for tile in self.tiles:
                if tile.md5 in self.symbols[digit]:
                    word.append(tile.matching_symbol)
                    break
            else:
                # Dump file only if the symbol is not found
                self.dump_tiles(self.path)
                raise VirtKeyboardError(f"Symbol '{digit}' not found; all symbol hashes are available in {self.path}")
        return self.codesep.join(word)
