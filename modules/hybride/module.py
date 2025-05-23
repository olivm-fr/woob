# Copyright(C) 2013      Bezleputh
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


from woob.capabilities.calendar import CATEGORIES, CapCalendarEvent
from woob.tools.backend import Module

from .browser import HybrideBrowser
from .calendar import HybrideCalendarEvent


__all__ = ["HybrideModule"]


class HybrideModule(Module, CapCalendarEvent):
    NAME = "hybride"
    DESCRIPTION = "hybride website"
    MAINTAINER = "Bezleputh"
    EMAIL = "carton_ben@yahoo.fr"
    LICENSE = "AGPLv3+"
    VERSION = "3.7"
    ASSOCIATED_CATEGORIES = [CATEGORIES.CINE]
    BROWSER = HybrideBrowser

    def search_events(self, query):
        if self.has_matching_categories(query):
            return self.browser.list_events(query.start_date, query.end_date, query.city, query.categories)

    def list_events(self, date_from, date_to=None):
        return self.browser.list_events(date_from, date_to)

    def get_event(self, _id):
        return self.browser.get_event(_id)

    def fill_obj(self, event, fields):
        return self.browser.get_event(event.id, event)

    OBJECTS = {HybrideCalendarEvent: fill_obj}
