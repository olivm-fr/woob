# Copyright(C) 2019      Vincent A
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

from woob.capabilities.bank.wealth import CapBankWealth
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword

from .browser import LendosphereBrowser


__all__ = ["LendosphereModule"]


class LendosphereModule(Module, CapBankWealth):
    NAME = "lendosphere"
    DESCRIPTION = "Lendosphere"
    MAINTAINER = "Vincent A"
    EMAIL = "dev@indigo.re"
    LICENSE = "LGPLv3+"
    VERSION = "3.7"

    BROWSER = LendosphereBrowser

    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="Email", masked=False),
        ValueBackendPassword("password", label="Mot de passe"),
    )

    def create_default_browser(self):
        return self.create_browser(
            self.config["login"].get(),
            self.config["password"].get(),
        )

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_investment(self, account):
        return self.browser.iter_investment(account)
