# Copyright(C) 2019 Sylvie Ye
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

# flake8: compatible

from io import BytesIO

from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText
from woob.browser.pages import HTMLPage, JsonPage
from woob.exceptions import ActionNeeded

from .transfer_page import TransferINGVirtKeyboard


class LoginPage(JsonPage):
    @property
    def is_logged(self):
        return "firstName" in self.doc

    def init_vk(self, img, password):
        pin_position = Dict("pinPositions")(self.doc)
        image = BytesIO(img)

        vk = TransferINGVirtKeyboard(image, cols=5, rows=2, browser=self.browser)
        password_random_coords = vk.password_tiles_coord(password)
        # pin positions (website side) start at 1, our positions start at 0
        return [password_random_coords[index - 1] for index in pin_position]

    def has_strong_authentication(self):
        # If this value is at False, this mean there is an OTP needed to login
        return not Dict("strongAuthenticationLoginExempted")(self.doc)

    def get_password_coord(self, img, password):
        assert "pinPositions" in self.doc, "Virtualkeyboard position has failed"
        assert "keyPadUrl" in self.doc, "Virtualkeyboard image url is missing"
        return self.init_vk(img, password)

    def get_keypad_url(self):
        return Dict("keyPadUrl")(self.doc)


class ActionNeededPage(HTMLPage):
    def on_load(self):
        if self.doc.xpath('//form//h1[1][contains(text(), "Accusé de reception du chéquier")]'):
            form = self.get_form(name="Alert")
            form["command"] = "validateAlertMessage"
            form["radioValide_1_2_40003039944"] = "Non"
            form.submit()
        elif self.doc.xpath('//p[@class="cddErrorMessage"]'):
            error_message = CleanText('//p[@class="cddErrorMessage"]')(self.doc)
            raise ActionNeeded(error_message)
        else:
            raise ActionNeeded(CleanText("//form//h1[1]")(self.doc))


class StopPage(HTMLPage):
    pass
