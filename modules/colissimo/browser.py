# -*- coding: utf-8 -*-

# Copyright(C) 2013-2014      Florent Fourcot
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

import re

from weboob.capabilities.parcel import Event, ParcelNotFound, Parcel
from weboob.browser import PagesBrowser, URL
from weboob.browser.pages import HTMLPage, JsonPage
from weboob.browser.profiles import Firefox

from dateutil.parser import parse as parse_date

__all__ = ['ColissimoBrowser']


class MainPage(HTMLPage):
    pass


class TrackingPage(JsonPage):
    def build_event(self, idx, item):
        event = Event(idx)
        event.date = parse_date(item["date"], ignoretz=True)
        event.activity = item["label"]
        return event

    STATUSES = {
        re.compile(
            r"remis au gardien ou"
            + r"|Votre colis est livré"
            + r"|Votre courrier a été distribué à l'adresse"
        ): Parcel.STATUS_ARRIVED,

        re.compile(
            r"pas encore pris en charge par La Poste"
            + r"|a été déposé dans un point postal"
            + r"|en cours de préparation"
        ): Parcel.STATUS_PLANNED,
    }

    def get_info(self, _id):
        if self.doc.get("shipment", {}).get("idShip", None) != _id:
            raise ParcelNotFound(f"Parcel ID {_id} not found.")
        p = Parcel(_id)
        events = [self.build_event(i, item) for i, item in enumerate(self.doc['shipment']['event'])]
        p.history = events

        first = events[0]
        p.info = first.activity
        context_data = self.doc["shipment"].get("contextData", {})
        delivery_mode = context_data.get("deliveryMode", None)
        if delivery_mode:
            p.info += " " + delivery_mode
        partner_reference = context_data.get("partner", {}).get("reference", None)
        if partner_reference:
            p.info += f" Partner reference: {partner_reference}"

        for pattern, status in self.STATUSES.items():
            if pattern.search(p.info):
                p.status = status
                break
        else:
            p.status = p.STATUS_IN_TRANSIT

        return p


class ColissimoBrowser(PagesBrowser):
    BASEURL = 'https://www.laposte.fr'
    PROFILE = Firefox()

    main_url = URL('/outils/suivre-vos-envois\?code=(?P<_id>.*)', MainPage)
    tracking_url = URL('https://api.laposte.fr/ssu/v1/suivi-unifie/idship/(?P<_id>.*)', TrackingPage)

    def get_tracking_info(self, _id):
        self.main_url.stay_or_go(_id=_id)
        self.tracking_url.stay_or_go(_id=_id, headers={"Accept": "application/json"})
        return self.page.get_info(_id)
