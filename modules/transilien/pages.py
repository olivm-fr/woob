# Copyright(C) 2010-2011 Julien Hébert, Romain Bignon
# Copyright(C) 2014 Benjamin Carton
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

import re

from woob.browser.elements import DictElement, ItemElement, TableElement, method
from woob.browser.filters.html import Link, TableCell
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, DateTime, Duration, Env, Filter, Format, Join, Regexp, Time
from woob.browser.pages import HTMLPage, JsonPage
from woob.capabilities import NotAvailable
from woob.capabilities.travel import Departure, RoadStep, Station


class RoadMapDuration(Duration):
    _regexp = re.compile(r"(?P<mn>\d+)")
    kwargs = {"minutes": "mn"}


class DepartureTypeFilter(Filter):
    def filter(self, el):
        result = []
        for img in el[0].iter(tag="img"):
            result.append(img.attrib["alt"])
        return " ".join(result)


class Child(Filter):
    def filter(self, el):
        return list(el[0].iterchildren())


class RoadMapPage(HTMLPage):
    def request_roadmap(self, station, arrival, departure_date, arrival_date):
        form = self.get_form('//form[@id="form_rechercheitineraire"]')
        form["depart"] = "%s" % station.name.replace(" ", "+")
        form["coordDepart"] = station._coord
        form["typeDepart"] = station._type_point
        form["arrivee"] = "%s" % arrival.name.replace(" ", "+")
        form["coordArrivee"] = arrival._coord
        form["typeArrivee"] = arrival._type_point
        if departure_date:
            form["jour"] = departure_date.strftime("%d/%m/%Y")
            form["horaire"] = departure_date.strftime("%H:%M")
            form["sens"] = 1
        elif arrival_date:
            form["jour"] = arrival_date.strftime("%d/%m/%Y")
            form["horaire"] = arrival_date.strftime("%H:%M")
            form["sens"] = -1

        form.submit()

    def is_ambiguous(self):
        return self.doc.xpath('//span[has-class("errormsg")]')

    def fix_ambiguity(self):
        form = self.get_form('//form[@id="cRechercheItineraire"]')
        if self.doc.xpath('//select[@id="gare_arrivee_ambigu"]'):
            form["coordArrivee"] = self.doc.xpath(
                '//select[@id="gare_arrivee_ambigu"]/option[@cat="STOP_AREA"]/@value'
            )[0]

        if self.doc.xpath('//select[@id="gare_depart_ambigu"]'):
            form["coordDepart"] = self.doc.xpath('//select[@id="gare_depart_ambigu"]/option[@cat="STOP_AREA"]/@value')[
                0
            ]

        form.submit()

    def get_roadmap(self):
        roadstep = None
        for step in self.doc.xpath('(//ol[@class="trajet_feuilleDeRoute transport"])[1]/li'):
            if step.attrib and "class" in step.attrib and step.attrib["class"] == "odd":

                if roadstep:
                    roadstep.end_time = Time(CleanText('./div/div[has-class("temps")]'))(step)
                    roadstep.arrival = CleanText('./div/div/div/div[@class="step_infos clearfix"]', default=None)(step)

                    yield roadstep

                roadstep = RoadStep()
                roadstep.start_time = Time(CleanText('./div/div[has-class("temps")]'))(step)
                roadstep.departure = CleanText('./div/div/div/div[@class="step_infos clearfix"]', default=None)(step)

            if not step.attrib:
                roadstep.line = (
                    CleanText('./div/div/div/div/div/div[@class="transport"]', default=None)(step)
                    or CleanText('./div/div/div/div[@class="step_infos clearfix"]', default=None)(step)
                    or Join("\n", "./div/div/div/div/div/ul/li/text()")(step)
                )

                roadstep.duration = RoadMapDuration(CleanText('./div/div[has-class("temps")]'))(step)

        del roadstep


class HorairesPage(HTMLPage):
    def get_departures(self, station, arrival, date):
        for table in self.doc.xpath('//table[@class="trajet_horaires trajet_etapes"]'):
            lignes = table.xpath('./tr[@class="ligne"]/th')
            arrives = table.xpath('./tr[@class="arrivee"]/td')
            departs = table.xpath('./tr[@class="depart"]/td')

            items = zip(lignes, arrives, departs)
            for item in items:
                departure = Departure()
                departure.id = Regexp(Link("./div/a"), ".*?vehicleJourneyExternalCode=(.*?)&.*?")(item[1])
                departure.departure_station = station
                departure.arrival_station = arrival
                hour, minute = CleanText("./div/a")(item[1]).split("h")
                departure.time = date.replace(hour=int(hour), minute=int(minute))
                hour, minute = CleanText("./div/a")(item[2]).split("h")
                departure.arrival_time = date.replace(hour=int(hour), minute=int(minute))
                departure.information = CleanText(".")(item[0])
                departure.type = DepartureTypeFilter(item)(self)
                yield departure


class StationsPage(JsonPage):

    @method
    class get_stations(DictElement):
        item_xpath = "gares"

        class item(ItemElement):
            klass = Station
            MapCategorieToTypePoint = {
                "StopArea": "STOP_AREA",
                "City": "CITY",
                "Site": "SITE_SEUL",
                "Address": "ADRESSE",
            }

            def condition(self):
                if self.env["only_station"]:
                    return Dict("entryPointType")(self.el) == "StopArea" and Dict("reseau")(self.el)[0]
                return True

            obj_name = CleanText(Dict("gare"))
            obj_id = CleanText(Dict("gare"), replace=[(" ", "-")])
            obj__coord = Format("%s_%s", Dict("coordLambertX"), Dict("coordLambertY"))

            def obj__type_point(self):
                key = Dict("entryPointType", default=None)(self)
                if key:
                    return self.MapCategorieToTypePoint[key]


class DeparturesPage2(HTMLPage):
    def get_potential_arrivals(self):
        arrivals = {}
        for el in self.doc.xpath('//select[@id="gare_arrive_ambigu"]/option'):
            arrivals[el.text] = el.attrib["value"]
        return arrivals

    def get_station_id(self):
        form = self.get_form('//form[@id="cfichehoraire"]')
        return form["departExternalCode"]

    def init_departure(self, station):
        form = self.get_form('//form[@id="cfichehoraire"]')
        form["depart"] = station
        form.submit()

    def get_departures(self, arrival, date):
        form = self.get_form('//form[@id="cfichehoraire"]')
        form["arrive"] = arrival
        if date:
            form["jourHoraire"] = date.day
            form["moiHoraire"] = f"{date.month}|{date.year}"
            form["heureHoraire"] = date.hour
            form["minuteHoraire"] = date.minute
        self.logger.debug(form)
        form.submit()


class DeparturesPage(HTMLPage):

    @method
    class get_departures(TableElement):
        head_xpath = '//table[@class="etat_trafic"][1]/thead/tr/th[@scope="col"]/text()'
        item_xpath = '//table[@class="etat_trafic"]/tr'

        col_type = "Ligne"
        col_info = "Nom du train"
        col_time = "Heure de départ"
        col_arrival = "Destination"
        col_plateform = "Voie/quai"
        col_id = "Gares desservies"

        class item(ItemElement):
            klass = Departure

            def condition(self):
                return len(self.el.xpath("./td")) >= 6

            obj_time = TableCell("time") & CleanText & DateTime | NotAvailable
            obj_type = DepartureTypeFilter(TableCell("type"))
            obj_departure_station = CleanText(Env("station"))
            obj_arrival_station = CleanText(TableCell("arrival"))
            obj_information = TableCell("time") & CleanText & Regexp(pattern=r"([^\d:]+)") | ""
            obj_plateform = CleanText(TableCell("plateform"))
            obj_id = Regexp(Link(Child(TableCell("id"))), r".*?numeroTrain=(.*?)&.*?")
