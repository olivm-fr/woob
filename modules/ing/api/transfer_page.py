# -*- coding: utf-8 -*-

# Copyright(C) 2019 Sylvie Ye
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import unicode_literals

import random
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageFilter

from weboob.tools.captcha.virtkeyboard import SimpleVirtualKeyboard
from weboob.browser.pages import LoggedPage, JsonPage
from weboob.browser.elements import method, DictElement, ItemElement
from weboob.browser.filters.json import Dict
from weboob.browser.filters.standard import Env, Field, Date, CleanText
from weboob.capabilities.bank import Recipient, Emitter


class TransferINGVirtKeyboard(SimpleVirtualKeyboard):
    tile_margin = 5
    convert = 'RGB'

    safe_tile_margin = 50
    small_img_size = (125, 50)  # original image size is (2420, 950)
    alter_img_params = {
        'radius': 2,
        'percent': 150,
        'threshold': 3,
        'limit_pixel': 125,
    }

    symbols = {
        '0': ('178b23cc890c258bd5594665f2df31c5', '9229a326c21320282f604c2e2d026c2b'),
        '1': 'd4a68e94d6267de3fa0c426aba0b8dc6',
        '2': '4a17f9e4088ef7d1a499a80bd7b56718',
        '3': 'f7f6364000813aec31e3d2df0dde8194',
        '4': '4f3161c7dacb0f8981dc8ad8321b7d22',
        '5': '6210d53a580d26fdbbf1e5ba62dc5f3d',
        '6': 'f748b7a25f12cc8b87deb22e33eff4a5',
        '7': '04a0f83158133ab5eeb69163f08c918f',
        '8': '859b2ad7dd70f429c761db4d625e3b57',
        '9': 'f249afdd16cf98e441e71d7a9dae5359',
    }

    # Clean image
    def alter_image(self):
        # original image size is (484, 190), save the original image
        self.original_image = self.image

        # create miniature of image to get more reliable hash
        self.image = self.image.resize(self.small_img_size, resample=Image.BILINEAR)
        # See ImageFilter.UnsharpMask from Pillow
        self.image = self.image.filter(ImageFilter.UnsharpMask(
            radius=self.alter_img_params['radius'],
            percent=self.alter_img_params['percent'],
            threshold=self.alter_img_params['threshold']
        ))

        def image_filter(px):
            if px <= self.alter_img_params['limit_pixel']:
                return 0
            return 255

        self.image = Image.eval(self.image, image_filter)

    def password_tiles_coord(self, password):
        # get image original size to get password coord
        image_width, image_height = self.original_image.size
        tile_width, tile_height = image_width // self.cols, image_height // self.rows

        password_tiles = []
        for digit in password:
            for tile in self.tiles:
                if tile.md5 in self.symbols[digit]:
                    password_tiles.append(tile)
                    break
            else:
                # Dump file only when the symbol is not found
                self.dump_tiles(self.path)
                raise Exception("Symbol '%s' not found; all symbol hashes are available in %s"
                                % (digit, self.path))

        formatted_password = []
        safe_margin = self.safe_tile_margin
        for tile in password_tiles:
            # default matching_symbol is str(range(cols*rows))
            x0 = (int(tile.matching_symbol) % self.cols) * tile_width
            y0 = (int(tile.matching_symbol) // self.cols) * tile_height
            tile_original_coords = (
                x0 + safe_margin, y0 + safe_margin,
                x0 + tile_width - safe_margin, y0 + tile_height - safe_margin,
            )
            formatted_password.append([
                random.uniform(tile_original_coords[0], tile_original_coords[2]),
                random.uniform(tile_original_coords[1], tile_original_coords[3]),
            ])
        return formatted_password


class DebitAccountsPage(LoggedPage, JsonPage):
    def get_debit_accounts_uid(self):
        return [Dict('uid')(recipient) for recipient in self.doc]

    @method
    class iter_emitters(DictElement):

        class item(ItemElement):

            klass = Emitter

            obj_id = Dict('uid')  # temporary ID, will be replaced by account ID from old website
            obj__partial_id = CleanText(Dict('label'), replace=[(' ', '')])
            obj_label = Dict('type/label')


class CreditAccountsPage(LoggedPage, JsonPage):
    @method
    class iter_recipients(DictElement):
        class item(ItemElement):
            def condition(self):
                return Dict('uid')(self) != Env('acc_uid')(self)

            klass = Recipient

            def obj__is_internal_recipient(self):
                return bool(Dict('ledgerBalance', default=None)(self))

            obj_id = Dict('uid')
            obj_enabled_at = datetime.now().replace(microsecond=0)

            def obj_label(self):
                if Field('_is_internal_recipient')(self):
                    return Dict('type/label')(self)
                return Dict('owner')(self)

            def obj_category(self):
                if Field('_is_internal_recipient')(self):
                    return 'Interne'
                return 'Externe'


class TransferPage(LoggedPage, JsonPage):
    @property
    def suggested_date(self):
        return Date(Dict('executionSuggestedDate'), dayfirst=True)(self.doc)

    def get_password_coord(self, password):
        assert Dict('pinValidateResponse', default=None)(self.doc), "Transfer virtualkeyboard position has failed"

        pin_position = Dict('pinValidateResponse/pinPositions')(self.doc)

        image_url = '/secure/api-v1%s' % Dict('pinValidateResponse/keyPadUrl')(self.doc)
        image = BytesIO(
            self.browser.open(
                image_url,
                headers={
                    'Referer': self.browser.absurl('/secure/transfers/new'),
                }
            ).content
        )

        vk = TransferINGVirtKeyboard(image, cols=5, rows=2, browser=self.browser)
        password_random_coords = vk.password_tiles_coord(password)
        # pin positions (website side) start at 1, our positions start at 0
        return [password_random_coords[index - 1] for index in pin_position]

    @property
    def transfer_is_validated(self):
        return Dict('acknowledged')(self.doc)

    def is_otp_authentication(self):
        return 'otpValidateResponse' in self.doc


class AddRecipientPage(LoggedPage, JsonPage):
    def check_recipient(self, recipient):
        rcpt = self.doc
        return rcpt['accountHolderName'] == recipient.label and rcpt['iban'] == recipient.iban


class OtpChannelsPage(LoggedPage, JsonPage):
    def get_sms_info(self):
        # receive a list of dict
        for element in self.doc:
            if element['type'] == 'SMS_MOBILE':
                return element
        raise AssertionError('No sms info found')


class ConfirmOtpPage(LoggedPage, JsonPage):
    pass
