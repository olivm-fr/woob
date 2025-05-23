# Copyright(C) 2010-2011 Julien Veyssier
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


from woob.capabilities.geolocip import CapGeolocIp
from woob.tools.backend import Module

from .browser import IpinfodbBrowser


__all__ = ["IpinfodbModule"]


class IpinfodbModule(Module, CapGeolocIp):
    NAME = "ipinfodb"
    MAINTAINER = "Julien Veyssier"
    EMAIL = "julien.veyssier@aiur.fr"
    VERSION = "3.7"
    LICENSE = "AGPLv3+"
    DESCRIPTION = "IPInfoDB IP addresses geolocation service"
    BROWSER = IpinfodbBrowser

    def get_location(self, ipaddr):
        return self.browser.get_location(ipaddr)
