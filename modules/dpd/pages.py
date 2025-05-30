# Copyright(C) 2015      Matthieu Weber
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

from dateutil.parser import parse as parse_date

from woob.browser.pages import JsonPage
from woob.capabilities.parcel import Event, Parcel, ParcelNotFound


STATUSES = {
    1: Parcel.STATUS_PLANNED,
    2: Parcel.STATUS_IN_TRANSIT,
    3: Parcel.STATUS_IN_TRANSIT,
    4: Parcel.STATUS_IN_TRANSIT,
    5: Parcel.STATUS_ARRIVED,
}


class SearchPage(JsonPage):
    def build_doc(self, text):
        from woob.tools.json import json

        return json.loads(text[1:-1])

    def get_info(self, _id):
        result_id = self.doc.get("TrackingStatusJSON", {}).get("shipmentInfo", {}).get("parcelNumber", None)
        if not result_id:
            raise ParcelNotFound("No such ID: %s" % _id)
        if not _id.startswith(result_id):
            raise ParcelNotFound(f"ID mismatch: expecting {_id}, got {result_id}")

        p = Parcel(_id)
        events = self.doc.get("TrackingStatusJSON", {}).get("statusInfos", [])
        p.history = [self.build_event(i, data) for i, data in enumerate(events)]
        p.status = self.guess_status(
            self.doc.get("TrackingStatusJSON", {}).get("shipmentInfo", {}).get("deliveryStatus")
        )
        p.info = p.history[-1].activity
        return p

    def guess_status(self, status_code):
        return STATUSES.get(status_code, Parcel.STATUS_UNKNOWN)

    def build_event(self, index, data):
        event = Event(index)
        date = "{} {}".format(data["date"], data["time"])
        event.date = parse_date(date, dayfirst=False)
        event.location = str(data["city"])
        event.activity = ", ".join([_["label"] for _ in data["contents"]])
        return event
