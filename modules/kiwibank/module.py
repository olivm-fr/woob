# Copyright(C) 2015 Cédric Félizard
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


from woob.capabilities.bank import CapBank
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword

from .browser import Kiwibank


__all__ = ["KiwibankModule"]


class KiwibankModule(Module, CapBank):
    NAME = "kiwibank"
    MAINTAINER = "Cédric Félizard"
    EMAIL = "cedric@felizard.fr"
    VERSION = "3.7"
    LICENSE = "AGPLv3+"
    DESCRIPTION = "Kiwibank"
    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="Access number", masked=False),
        ValueBackendPassword("password", label="Password"),
    )
    BROWSER = Kiwibank

    def create_default_browser(self):
        return self.create_browser(self.config["login"].get(), self.config["password"].get())

    def iter_accounts(self):
        return self.browser.get_accounts()

    def iter_history(self, account):
        yield from self.browser.get_history(account)
