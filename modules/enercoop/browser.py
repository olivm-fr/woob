# -*- coding: utf-8 -*-

# Copyright(C) 2020      Vincent A
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import unicode_literals

import datetime

from weboob.browser import LoginBrowser, URL, need_login
from weboob.capabilities.base import find_object
from weboob.capabilities.gauge import Gauge, GaugeSensor

from .pages import (
    BillsPage, ProfilePage,
    YearlyPage, MonthlyPage, DailyPage, HourlyPage,
)


class EnercoopBrowser(LoginBrowser):
    BASEURL = 'https://espace-client.enercoop.fr'

    login = URL('/login')
    bills = URL(
        r'/mon-espace/factures/',
        r'/mon-espace/factures/\?c=(?P<id>\d+)',
        BillsPage
    )

    profile = URL(
        r'/mon-espace/compte/',
        r'/mon-espace/compte/\?c=(?P<id>\d+)',
        ProfilePage
    )

    yearly = URL(r"https://mon-espace.enercoop.fr/(?P<contract>\d+)/conso_glo$", YearlyPage)
    monthly = URL(r"https://mon-espace.enercoop.fr/(?P<contract>\d+)/conso_glo/(?P<year>\d{4})$", MonthlyPage)
    daily = URL(
        r"https://mon-espace.enercoop.fr/(?P<contract>\d+)/conso_glo/(?P<year>\d{4})/(?P<month>\d{2})$",
        DailyPage
    )
    hourly = URL(
        r"https://mon-espace.enercoop.fr/(?P<contract>\d+)/conso_glo/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})$",
        HourlyPage
    )

    def do_login(self):
        self.login.go(data={
            'email': self.username,
            'password': self.password,
        })

    def export_session(self):
        return {
            **super().export_session(),
            'url': self.bills.build(),
        }

    @need_login
    def iter_subscription(self):
        self.bills.go()
        subs = {sub.id: sub for sub in self.page.iter_other_subscriptions()}
        if subs:
            self.bills.go(id=next(iter(subs)))
            subs.update({sub.id: sub for sub in self.page.iter_other_subscriptions()})

            for sub in subs:
                self.profile.go(id=sub)
                self.page.fill_sub(subs[sub])

            return subs.values()

        raise NotImplementedError("how to get info when no selector?")

    @need_login
    def iter_documents(self, id):
        self.bills.go(id=id)
        return self.page.iter_documents()

    @need_login
    def download_document(self, document):
        return self.open(document.url).content

    @need_login
    def iter_gauges(self):
        # TODO implement for multiple contracts
        # and for disabled contracts, consumption pages won't work
        self.profile.go()
        pdl = self.page.get_pdl_number()
        return [Gauge.from_dict({
            "id": f"{pdl}",
            "name": "Consommation",
            "object": "Consommation",
        })]

    consumption_periods = {
        "yearly": "annuelle",
        "monthly": "mensuelle",
        "daily": "quotidienne",
        "hourly": "par demie-heure",
    }

    def iter_sensors(self, id, pattern=None):
        g = find_object(self.iter_gauges(), id=id)
        assert g

        return [
            GaugeSensor.from_dict({
                "id": f"{id}.c.{subid}",
                "name": f"Consommation électrique {name}",
                "unit": "kWh",
                "gaugeid": id,
            })
            for subid, name in self.consumption_periods.items()
        ]

    @need_login
    def iter_sensor_history(self, id):
        pdl, sensor_type, subid = id.split(".")
        assert sensor_type == "c"
        assert subid in ("yearly", "monthly", "daily", "hourly")

        # can't fetch stats of today, use yesterday (and the corresponding month/year)
        max_date = datetime.date.today() - datetime.timedelta(days=1)

        url_args = {}
        for unit in ("year", "month", "day"):
            if subid[0] != unit[0]:
                url_args[unit] = str(getattr(max_date, unit)).zfill(2)
            else:
                break

        getattr(self, subid).go(contract=pdl, **url_args)
        for measure in self.page.iter_sensor_history():
            if measure.date.date() > max_date:
                continue
            yield measure
