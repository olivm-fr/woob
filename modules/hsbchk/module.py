# Copyright(C) 2012-2013 Romain Bignon
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


from woob.capabilities.bank import CapBankWealth
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword

from .browser import HSBCHK


__all__ = ["HSBCHKModule"]


class HSBCHKModule(Module, CapBankWealth):
    NAME = "hsbchk"
    MAINTAINER = "sinopsysHK"
    EMAIL = "sinofwd@gmail.com"
    VERSION = "3.7"
    LICENSE = "LGPLv3+"
    DESCRIPTION = "HSBC Hong Kong"
    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="User identifier", masked=False),
        ValueBackendPassword("password", label="Password"),
        ValueBackendPassword("secret", label="Memorable answer"),
    )
    BROWSER = HSBCHK

    def create_default_browser(self):
        return self.create_browser(
            self.config["login"].get(), self.config["password"].get(), self.config["secret"].get()
        )

    def iter_accounts(self):
        yield from self.browser.iter_accounts()

    def iter_history(self, account):
        yield from self.browser.get_history(account)

    def iter_investment(self, account):
        raise NotImplementedError

    def iter_coming(self, account):
        yield from self.browser.get_history(account, coming=True)
