# Copyright(C) 2015      Oleg Plakhotniuk
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

from woob.capabilities.bank import CapBank
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword

from .browser import VicSecCard


__all__ = ["VicSecCardModule"]


class VicSecCardModule(Module, CapBank):
    NAME = "vicseccard"
    MAINTAINER = "Oleg Plakhotniuk"
    EMAIL = "olegus8@gmail.com"
    VERSION = "3.7"
    LICENSE = "LGPLv3+"
    DESCRIPTION = "Victoria's Secret Angel Card"
    CONFIG = BackendConfig(
        ValueBackendPassword("username", label="User name", masked=False),
        ValueBackendPassword("password", label="Password"),
    )
    BROWSER = VicSecCard

    def create_default_browser(self):
        return self.create_browser(self.config["username"].get(), self.config["password"].get())

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_history(self, account):
        return self.browser.iter_history(account)
