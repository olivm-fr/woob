# Copyright(C) 2017      Phyks (Lucas Verney)
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


from woob.browser import URL, PagesBrowser

from .pages import MeteoPage


class RATPBrowser(PagesBrowser):
    BASEURL = "https://www.ratp.fr"

    meteo = URL("/meteo", MeteoPage)

    def list_gauges(self):
        """
        Get all the available lines.
        """
        self.meteo.go()
        return self.page.fetch_lines()

    def get_status(self, sensor):
        """
        Get current status of a sensor.
        """
        self.meteo.go()
        return self.page.fetch_status(line=sensor.replace("_sensor", ""))
