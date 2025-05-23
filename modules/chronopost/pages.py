# Copyright(C) 2013      Romain Bignon
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


from woob.browser.elements import ItemElement, ListElement, method
from woob.browser.filters.standard import CleanText, DateTime, Env
from woob.browser.pages import HTMLPage, JsonPage
from woob.capabilities import NotAvailable
from woob.capabilities.parcel import Event, Parcel, ParcelNotFound
from woob.tools.date import parse_french_date


class TrackPage(JsonPage):
    ENCODING = "utf-8"

    def build_doc(self, text):
        doc = super().build_doc(text)

        content = "".join([doc["top"], doc["tab"]])
        html_page = HTMLPage(self.browser, self.response)
        return html_page.build_doc(content.encode(self.encoding))

    @method
    class get_parcel(ItemElement):
        klass = Parcel

        def parse(self, el):
            error = CleanText('//div[has-class("ch-colis-information")]')(el)
            if "pas d'information" in error:
                raise ParcelNotFound(error)

        obj_id = Env("id")
        obj_info = CleanText(
            '//div[has-class("ch-block-subtitle-content")]//div[has-class("ch-colis-information")]/text()'
        )
        obj_arrival = CleanText(
            '//div[has-class("ch-block-subtitle-content")]//div[has-class("ch-colis-information")]/text()[3]',
            replace=[("\xe0", "")],
            default=NotAvailable,
        ) & DateTime(dayfirst=True, parse_func=parse_french_date, default=NotAvailable, strict=False)

        def obj_status(self):
            el = self.el.xpath('//div[has-class("ch-suivi-colis-light-info") and has-class("active")]')[0]
            if "last" in el.attrib["class"]:
                return Parcel.STATUS_ARRIVED
            if "first" in el.attrib["class"]:
                return Parcel.STATUS_PLANNED

            return Parcel.STATUS_IN_TRANSIT

        class obj_history(ListElement):
            item_xpath = '//table[has-class("ch-block-suivi-tab")]//tr[has-class("toggleElmt")]'

            class item(ItemElement):
                klass = Event

                obj_date = CleanText(".//td[1]") & DateTime(dayfirst=True, parse_func=parse_french_date, strict=False)
                obj_location = CleanText(".//td[2]/text()[following-sibling::br]")
                obj_activity = CleanText(".//td[2]/text()[preceding-sibling::br]")
