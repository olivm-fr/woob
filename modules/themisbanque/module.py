# Copyright(C) 2015      Romain Bignon
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
from woob.capabilities.profile import CapProfile
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword

from .browser import ThemisBrowser


__all__ = ["ThemisModule"]


class ThemisModule(Module, CapBank, CapProfile):
    NAME = "themisbanque"
    DESCRIPTION = "Themis"
    MAINTAINER = "Romain Bignon"
    EMAIL = "romain@weboob.org"
    LICENSE = "LGPLv3+"
    VERSION = "3.7"
    CONFIG = BackendConfig(
        ValueBackendPassword("login", label="Numéro d'abonné", masked=False),
        ValueBackendPassword("password", label="Code secret"),
    )

    BROWSER = ThemisBrowser

    def create_default_browser(self):
        return self.create_browser(self.config["login"].get(), self.config["password"].get())

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_coming(self, account):
        raise NotImplementedError()

    def iter_history(self, account):
        return self.browser.get_history(account)

    def iter_investment(self, account):
        raise NotImplementedError()

    def get_profile(self):
        return self.browser.get_profile()
