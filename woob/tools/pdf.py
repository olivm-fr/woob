# Copyright(C) 2014 Oleg Plakhotniuk
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

import logging
import os
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from http.cookiejar import Cookie
from io import BytesIO, StringIO
from tempfile import mkstemp
from typing import TYPE_CHECKING, Any, Callable, NamedTuple, Optional, Protocol, TypeVar, cast, overload


if TYPE_CHECKING:
    from pdfkit import Configuration
    from pdfminer.layout import LTChar, LTCurve, LTLine, LTPage, LTRect, LTTextBox, LTTextLine

    from woob.browser.browsers import Browser  # Avoid circular import at runtime


__all__ = ["decompress_pdf", "get_pdf_rows"]


def decompress_pdf(inpdf: bytes) -> bytes:
    """
    Takes PDF file contents as a string and returns decompressed version
    of the file contents, suitable for text parsing.

    External dependencies:
    MuPDF (https://www.mupdf.com).
    """

    inh, inname = mkstemp(suffix=".pdf")
    outh, outname = mkstemp(suffix=".pdf")
    os.write(inh, inpdf)
    os.close(inh)
    os.close(outh)

    subprocess.call(["mutool", "clean", "-d", inname, outname])

    with open(outname, "rb") as f:
        outpdf = f.read()
    os.remove(inname)
    os.remove(outname)
    return outpdf


Point2D = tuple[float, float]
T = TypeVar("T")


class Rect(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float


class TextRect(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def almost_eq(a: float, b: float) -> bool:
    return abs(a - b) < 2


def lt_to_coords(obj: LTCurve, ltpage: LTPage) -> Rect:
    # in a pdf, 'y' coords are bottom-to-top
    # in a pdf, coordinates are very often almost equal but not strictly equal

    x0 = min(obj.x0, obj.x1)
    y0 = min(ltpage.y1 - obj.y0, ltpage.y1 - obj.y1)
    x1 = max(obj.x0, obj.x1)
    y1 = max(ltpage.y1 - obj.y0, ltpage.y1 - obj.y1)

    x0 = round(x0)
    y0 = round(y0)
    x1 = round(x1)
    y1 = round(y1)

    # in a pdf, straight lines are actually rects, make them as thin as possible
    if almost_eq(x1, x0):
        x1 = x0
    if almost_eq(y1, y0):
        y1 = y0

    return Rect(x0, y0, x1, y1)


def lttext_to_multilines(obj: LTTextBox | LTTextLine | LTChar, ltpage: LTPage) -> Iterator[TextRect]:
    # text lines within 'obj' are probably the same height
    x0 = min(obj.x0, obj.x1)
    y0 = min(ltpage.y1 - obj.y0, ltpage.y1 - obj.y1)
    x1 = max(obj.x0, obj.x1)
    y1 = max(ltpage.y1 - obj.y0, ltpage.y1 - obj.y1)

    lines = obj.get_text().rstrip("\n").split("\n")
    h = (y1 - y0) / len(lines)

    for n, line in enumerate(lines):
        yield TextRect((x0), (y0 + n * h), (x1), (y0 + n * h + h), line)


# fuzzy floats to smooth comparisons because lines are actually rects
# and seemingly-contiguous lines are actually not contiguous
class ApproxFloat(float):
    def __eq__(self, other: object) -> bool:
        if isinstance(other, float):
            return almost_eq(self, float(other))
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __lt__(self, other: float) -> bool:
        return self - other < 0 and self != other

    def __le__(self, other: float) -> bool:
        return self - other <= 0 or self == other

    def __gt__(self, other: float) -> bool:
        return not self <= other

    def __ge__(self, other: float) -> bool:
        return not self < other


ANGLE_VERTICAL = 0
ANGLE_HORIZONTAL = 1
ANGLE_OTHER = 2


def angle(r: LTLine) -> int:
    if r.x0 == r.x1:
        return ANGLE_VERTICAL
    elif r.y0 == r.y1:
        return ANGLE_HORIZONTAL
    return ANGLE_OTHER


VT = TypeVar("VT")


class ApproxVecDict(dict[Point2D, VT]):
    # since coords are never strictly equal, search coords around
    # store vectors and points

    def __getitem__(self, coords: Point2D) -> VT:
        x, y = coords
        for i in (0, -1, 1):
            for j in (0, -1, 1):
                try:
                    return super().__getitem__((x + i, y + j))
                except KeyError:
                    pass
        raise KeyError()

    @overload
    def get(self, k: Point2D) -> VT | None: ...

    @overload
    def get(self, k: Point2D, default: T) -> T | VT: ...

    def get(self, k: Point2D, default: T | None = None) -> T | VT | None:
        try:
            return self[k]
        except KeyError:
            return default


class ApproxRectDict(dict[Rect, Optional[Rect]]):
    # like ApproxVecDict, but store rects
    def __getitem__(self, coords: Rect) -> Rect | None:
        x0, y0, x1, y1 = coords

        for i in (0, -1, 1):
            for j in (0, -1, 1):
                if x0 == x1:
                    for j2 in (0, -1, 1):
                        try:
                            return super().__getitem__(Rect(x0 + i, y0 + j, x0 + i, y1 + j2))
                        except KeyError:
                            pass
                elif y0 == y1:
                    for i2 in (0, -1, 1):
                        try:
                            return super().__getitem__(Rect(x0 + i, y0 + j, x1 + i2, y0 + j))
                        except KeyError:
                            pass
                else:
                    return super().__getitem__(Rect(x0, y0, x1, y1))

        raise KeyError()


def uniq_lines(lines: Iterable[LTCurve]) -> list[Rect]:
    new = ApproxRectDict()
    for line in lines:
        line = Rect(*line)
        try:
            new[line]
        except KeyError:
            new[line] = None
    return [Rect(*k) for k in new.keys()]


def build_rows(lines: Iterable[LTCurve]) -> list[list[Rect]]:
    points: ApproxVecDict[tuple[list[LTCurve], list[LTCurve]]] = ApproxVecDict()

    # for each top-left point, build tuple with lines going down and lines going right
    for line in lines:
        a = angle(line)
        if a not in (ANGLE_HORIZONTAL, ANGLE_VERTICAL):
            continue

        coord = (line.x0, line.y0)
        plines = points.get(coord)
        if plines is None:
            plines = points[coord] = ([], [])

        plines[a].append(line)

    boxes: ApproxVecDict[list[Rect]] = ApproxVecDict()
    for plines in points.values():
        if not (plines[ANGLE_HORIZONTAL] and plines[ANGLE_VERTICAL]):
            continue

        plines[ANGLE_HORIZONTAL].sort(key=lambda pline: (pline.y0, pline.x1))
        plines[ANGLE_VERTICAL].sort(key=lambda pline: (pline.x0, pline.y1))

        for hline in plines[ANGLE_HORIZONTAL]:
            try:
                vparallels = points[hline.x1, hline.y0][ANGLE_VERTICAL]
            except KeyError:
                continue
            if not vparallels:
                continue

            for vline in plines[ANGLE_VERTICAL]:
                try:
                    hparallels = points[vline.x0, vline.y1][ANGLE_HORIZONTAL]
                except KeyError:
                    continue
                if not hparallels:
                    continue

                hparallels = [hpar for hpar in hparallels if almost_eq(hpar.x1, hline.x1)]
                if not hparallels:
                    continue
                vparallels = [vpar for vpar in vparallels if almost_eq(vpar.y1, vline.y1)]
                if not vparallels:
                    continue

                assert len(hparallels) == 1 and len(vparallels) == 1
                assert almost_eq(hparallels[0].y0, vparallels[0].y1)
                assert almost_eq(vparallels[0].x0, hparallels[0].x1)

                box = Rect(hline.x0, hline.y0, hline.x1, vline.y1)
                boxes.setdefault((vline.y0, vline.y1), []).append(box)

    rows = list(boxes.values())
    new_rows = []
    for row in rows:
        row.sort(key=lambda box: box.x0)
        if row:
            row = [row[0]] + [c for n, c in enumerate(row[1:], 1) if row[n - 1].x0 != c.x0]
        new_rows.append(row)

    rows = new_rows
    rows.sort(key=lambda row: row[0].y0)

    return rows


def find_in_table(rows: Iterable[LTCurve], rect: LTRect) -> tuple[int, int] | None:
    for j, row in enumerate(rows):
        if ApproxFloat(row[0].y0) > rect.y1:
            break

        if not (ApproxFloat(row[0].y0) <= rect.y0 and ApproxFloat(row[0].y1) >= rect.y1):
            continue

        for i, box in enumerate(row):
            if ApproxFloat(box.x0) <= rect.x0 and ApproxFloat(box.x1) >= rect.x1:
                return i, j

    return None


def arrange_texts_in_rows(rows: Iterable[LTCurve], trects: Iterable[LTRect]) -> list[list[list[str]]]:
    table: list[list[list[str]]] = [[[] for _ in row] for row in rows]

    for trect in trects:
        pos = find_in_table(rows, trect)
        if not pos:
            continue
        table[pos[1]][pos[0]].append(trect.text)
    return table


LOGGER = logging.getLogger(__name__)
DEBUGFILES = logging.DEBUG - 1


def get_pdf_rows(data: bytes, miner_layout: bool = True) -> Iterator[list[list[list[str]]]]:
    """
    Takes PDF file content as string and yield table row data for each page.

    For each page in the PDF, the function yields a list of rows.
    Each row is a list of cells. Each cell is a list of strings present in the cell.
    Note that the rows may belong to different tables.

    There are no logic tables in PDF format, so this parses PDF drawing instructions
    and tries to find rectangles and arrange them in rows, then arrange text in
    the rectangles.

    External dependencies:
    PDFMiner (https://github.com/euske/pdfminer).
    """

    try:
        from pdfminer.pdfparser import PDFParser, PDFSyntaxError
    except ImportError:
        raise ImportError("Please install python3-pdfminer")

    try:
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfpage import PDFPage

        newapi = True
    except ImportError:
        from pdfminer.pdfparser import PDFDocument

        newapi = False
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LAParams, LTChar, LTCurve, LTLine, LTRect, LTTextBox, LTTextLine
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager

    parser = PDFParser(BytesIO(data))
    try:
        if newapi:
            doc = PDFDocument(parser)
        else:
            doc = PDFDocument()
            parser.set_document(doc)
            doc.set_parser(parser)
    except PDFSyntaxError:
        return

    rsrcmgr = PDFResourceManager()
    if miner_layout:
        device = PDFPageAggregator(rsrcmgr, laparams=LAParams())
    else:
        device = PDFPageAggregator(rsrcmgr)

    interpreter = PDFPageInterpreter(rsrcmgr, device)
    if newapi:
        pages = PDFPage.get_pages(BytesIO(data), check_extractable=True)
    else:
        doc.initialize()
        pages = doc.get_pages()

    if LOGGER.isEnabledFor(DEBUGFILES):
        import random
        import tempfile

        import PIL.Image as Image
        import PIL.ImageDraw as ImageDraw

        path = tempfile.mkdtemp(prefix="pdf")

    for npage, page in enumerate(pages):
        LOGGER.debug("processing page %s", npage)
        interpreter.process_page(page)
        page_layout = device.get_result()

        texts = sum(
            [
                list(lttext_to_multilines(obj, page_layout))
                for obj in page_layout._objs
                if isinstance(obj, (LTTextBox, LTTextLine, LTChar))
            ],
            [],
        )
        LOGGER.debug("found %d text objects", len(texts))
        if LOGGER.isEnabledFor(DEBUGFILES):
            img = Image.new("RGB", (int(page.mediabox[2]), int(page.mediabox[3])), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for t in texts:
                color = (random.randint(127, 255), random.randint(127, 255), random.randint(127, 255))
                draw.rectangle((t.x0, t.y0, t.x1, t.y1), outline=color)
                draw.text((t.x0, t.y0), t.text.encode("utf-8"), color)
            fpath = "%s/1text-%03d.png" % (path, npage)
            img.save(fpath)
            LOGGER.log(DEBUGFILES, "saved %r", fpath)

        if not miner_layout:
            texts.sort(key=lambda t: (t.y0, t.x0))

        # TODO filter ltcurves that are not lines?
        # TODO convert rects to 4 lines?
        lines = [
            lt_to_coords(obj, page_layout) for obj in page_layout._objs if isinstance(obj, (LTRect, LTLine, LTCurve))
        ]
        LOGGER.debug("found %d lines", len(lines))
        if LOGGER.isEnabledFor(DEBUGFILES):
            img = Image.new("RGB", (int(page.mediabox[2]), int(page.mediabox[3])), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for line in lines:
                color = (random.randint(127, 255), random.randint(127, 255), random.randint(127, 255))
                draw.rectangle((line.x0, line.y0, line.x1, line.y1), outline=color)
            fpath = "%s/2lines-%03d.png" % (path, npage)
            img.save(fpath)
            LOGGER.log(DEBUGFILES, "saved %r", fpath)

        lines = list(uniq_lines(lines))
        LOGGER.debug("found %d unique lines", len(lines))

        rows = build_rows(lines)
        LOGGER.debug("built %d rows (%d boxes)", len(rows), sum(len(row) for row in rows))
        if LOGGER.isEnabledFor(DEBUGFILES):
            img = Image.new("RGB", (int(page.mediabox[2]), int(page.mediabox[3])), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for r in rows:
                for b in r:
                    color = (random.randint(127, 255), random.randint(127, 255), random.randint(127, 255))
                    draw.rectangle((b.x0 + 1, b.y0 + 1, b.x1 - 1, b.y1 - 1), outline=color)
            fpath = "%s/3rows-%03d.png" % (path, npage)
            img.save(fpath)
            LOGGER.log(DEBUGFILES, "saved %r", fpath)

        textrows = arrange_texts_in_rows(rows, texts)
        LOGGER.debug("assigned %d strings", sum(sum(len(c) for c in r) for r in textrows))
        if LOGGER.isEnabledFor(DEBUGFILES):
            img = Image.new("RGB", (int(page.mediabox[2]), int(page.mediabox[3])), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for row, trow in zip(rows, textrows):
                for b, tlines in zip(row, trow):
                    color = (random.randint(127, 255), random.randint(127, 255), random.randint(127, 255))
                    draw.rectangle((b.x0 + 1, b.y0 + 1, b.x1 - 1, b.y1 - 1), outline=color)
                    draw.text((b.x0 + 1, b.y0 + 1), "\n".join(tlines).encode("utf-8"), color)
            fpath = "%s/4cells-%03d.png" % (path, npage)
            img.save(fpath)
            LOGGER.log(DEBUGFILES, "saved %r", fpath)

        yield textrows
    device.close()


# Export part #


class PdfkitProtocol(Protocol):
    """Backward compatible type annotation for pdfkit.api.from_*."""

    def __call__(
        self,
        input: str,
        output_path: str | bool | None = None,
        options: Mapping[str, Any] | None = None,
        toc: Mapping[str, Any] | None = None,
        cover: str | None = None,
        configuration: Configuration | None = None,
        cover_first: bool = False,
        verbose: bool = False,
    ) -> bool: ...


def html_to_pdf(
    browser: Browser,
    url: str | None = None,
    data: str | None = None,
    extra_options: Mapping[str, Any] | None = None,
) -> bool:
    """
    Convert html to PDF.

    :param browser: browser instance
    :param url: link to the html ressource
    :param data: HTML content
    :return: the document converted in PDF
    :rtype: bytes
    """
    try:
        import pdfkit  # https://pypi.python.org/pypi/pdfkit
    except ImportError:
        raise ImportError("Please install python3-pdfkit")

    assert (url or data) and not (url and data), "Please give only url or data parameter"

    callback: PdfkitProtocol = pdfkit.from_url if url else pdfkit.from_string
    options = {}

    try:
        cookies = browser.session.cookies
    except AttributeError:
        pass
    else:
        options.update(
            {
                "cookie": [(cookie, value) for cookie, value in cookies.items() if value],  # cookies of browser
            }
        )

    if extra_options:
        options.update(extra_options)

    return callback(cast(str, url or data), False, options=options)


class BlinkPdfError(Exception):
    pass


def blinkpdf(
    browser: Browser,
    url: str,
    extra_options: Mapping[str, Any] | None = None,
    filter_cookie: Callable[[Cookie], bool] | None = None,
    start_xvfb: bool = True,
    timeout: int = 120,
) -> bytes:
    # - xvfb is required for blinkpdf 1.0, but not for 1.1
    # - xvfb is not necessary for QtWebEngine 5.14, but it is for 5.11, which is the version
    #   available on the ppa for debian/buster stable

    xvfb_exists = False
    blinkpdf_exists = False
    paths = os.getenv("PATH", os.defpath).split(os.pathsep)
    for path in paths:
        fpath = os.path.join(path, "xvfb-run")
        if os.path.exists(fpath) and os.access(fpath, os.X_OK):
            xvfb_exists = True
        fpath = os.path.join(path, "blinkpdf")
        if os.path.exists(fpath) and os.access(fpath, os.X_OK):
            blinkpdf_exists = True

    if (not xvfb_exists and start_xvfb) or not blinkpdf_exists:
        raise NotImplementedError()

    args = []
    for c in browser.session.cookies:
        if c.value:
            if not filter_cookie or filter_cookie(c):
                args.append("--cookie")
                args.append(f"{c.name}={c.value}")

    for key, value in browser.session.headers.items():
        args.append("--header")
        args.append(f"{key}={value}")

    if extra_options and "run-script" in extra_options:
        args.append("--run-script")
        args.append(extra_options["run-script"][0])

    args.append(url)
    args.append("-")  # - : don't write it on disk, simply return value

    if start_xvfb:
        # put a very small resolution to reduce used memory, because we don't really need it, it doesn't influence pdf size
        # -screen 0 width*height*bit depth
        prepend = ["xvfb-run", "-a", "-s", "-screen 0 2x2x8", "blinkpdf"]
    else:
        prepend = ["blinkpdf"]

    cmd = list(prepend) + list(args)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        # Will raise a TimeoutExpired after timeout seconds
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # A timeout doesn't kill the child process
        proc.kill()
        # Log the error output after the end of the process. 20 seconds should
        # be enough for the process to terminate cleanly
        _, stderr = proc.communicate(timeout=20)
        LOGGER.error("The blinkpdf process took too long to complete. Error output: %s", stderr.decode("utf-8"))
        raise

    if proc.returncode != 0:
        raise BlinkPdfError("command returned non-zero exit status 1: %s" % stderr.decode("utf-8"))
    return stdout


# extract all text from PDF
def extract_text(data: bytes) -> str | None:
    try:
        try:
            from pdfminer.pdfdocument import PDFDocument
            from pdfminer.pdfpage import PDFPage

            newapi = True
        except ImportError:
            from pdfminer.pdfparser import PDFDocument

            newapi = False
        from pdfminer.converter import TextConverter
        from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
        from pdfminer.pdfparser import PDFParser, PDFSyntaxError
    except ImportError:
        raise ImportError("Please install python3-pdfminer to parse PDF")
    else:
        parser = PDFParser(BytesIO(data))
        try:
            if newapi:
                doc = PDFDocument(parser)
            else:
                doc = PDFDocument()
                parser.set_document(doc)
                doc.set_parser(parser)
        except PDFSyntaxError:
            return None

        rsrcmgr = PDFResourceManager()
        out = StringIO()
        device = TextConverter(rsrcmgr, out)
        interpreter = PDFPageInterpreter(rsrcmgr, device)
        if newapi:
            pages = PDFPage.create_pages(doc)
        else:
            doc.initialize()
            pages = doc.get_pages()
        for page in pages:
            interpreter.process_page(page)

        return out.getvalue()
