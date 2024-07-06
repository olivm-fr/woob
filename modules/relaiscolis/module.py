# -*- coding: utf-8 -*-

# Copyright(C) 2017      Mickaël Thomas
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

from woob.tools.backend import Module, BackendConfig
from woob.capabilities.base import NotAvailable
from woob.capabilities.parcel import CapParcel, Parcel, ParcelNotFound
from woob.tools.value import Value
from woob.exceptions import BrowserQuestion

from .browser import RelaiscolisBrowser

__all__ = ['RelaiscolisModule']


class RelaiscolisModule(Module, CapParcel):
    NAME = 'relaiscolis'
    DESCRIPTION = 'Relais colis parcel tracking website'
    MAINTAINER = 'Mickaël Thomas'
    EMAIL = 'mickael9@gmail.com'
    LICENSE = 'AGPLv3+'
    VERSION = '3.6'
    CONFIG = BackendConfig(
        Value('last_name', label='Last name'),
        Value('merchant', label='Merchant (leave blank)', default=''),
    )

    BROWSER = RelaiscolisBrowser

    def get_parcel_tracking(self, _id):
        """
        Get information about a parcel.

        :param _id: _id of the parcel
        :type _id: :class:`str`
        :rtype: :class:`Parcel`
        :raises: :class:`ParcelNotFound`
        """
        # Tracking number format:
        # - 2 chars: optional merchant identifier (eg, AM for Amazon, 85 for cdiscount, ...)
        # - 10 digits: shipment tracking number
        # - 2 digits: optional suffix, seems to always be "01" when present but is never sent to the API
        #
        # Many merchants seem to give only the 10 digits tracking number so the user needs to
        # manually select the merchant from a list in that case.

        merchant = None
        code = None

        _id = _id.strip().upper()

        if len(_id) == 10:
            code = _id
        elif len(_id) in (12, 14):
            merchant = _id[:2]
            code = _id[2:12]
        else:
            raise ParcelNotFound(
                "Tracking number must be 10, 12 or 14 characters long."
            )

        merchant = merchant or self.config['merchant'].get()

        if not merchant:
            # No merchant info in the tracking number
            # we have to ask the user to select it
            merchants = self.browser.get_merchants()
            raise BrowserQuestion(Value(
                'merchant', label='Merchant prefix (prepend to tracking number): ', tiny=False,
                choices=merchants,
            ))

        self.config['merchant'].set(None)
        name = self.config['last_name'].get()[:4].ljust(4).upper()

        events = list(self.browser.iter_events(merchant, code, name))

        parcel = Parcel(merchant + code)
        parcel.arrival = NotAvailable

        # This is what the legacy tracking website used to show
        # when there are no events yet
        parcel.info = "Votre commande est en cours d'acheminement dans notre réseau."

        parcel.history = events
        parcel.status = Parcel.STATUS_IN_TRANSIT

        if not events:
            parcel.status = Parcel.STATUS_PLANNED
            return parcel

        parcel.info = events[0].activity

        arrived_event = next((event for event in events
                             if "Votre colis est disponible" in event.activity),
                             None)

        if arrived_event:
            parcel.status = Parcel.STATUS_ARRIVED
            parcel.arrival = arrived_event.date

        return parcel
