# Copyright(C) 2014-2015      Oleg Plakhotniuk
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

from .browser import AmazonStoreCard


__all__ = ["AmazonStoreCardModule"]


class AmazonStoreCardModule(Module, CapBank):
    NAME = "amazonstorecard"
    MAINTAINER = "Oleg Plakhotniuk"
    EMAIL = "olegus8@gmail.com"
    VERSION = "3.7"
    LICENSE = "LGPLv3+"
    DESCRIPTION = "Amazon Store Card"
    CONFIG = BackendConfig(
        ValueBackendPassword("username", label="User ID", masked=False),
        ValueBackendPassword("password", label="Password"),
        ValueBackendPassword("phone", label="Phone to send verification code to", masked=False),
        ValueBackendPassword("code_file", label="File to read the verification code from", masked=False),
    )
    BROWSER = AmazonStoreCard

    def create_default_browser(self):
        return self.create_browser(
            username=self.config["username"].get(),
            password=self.config["password"].get(),
            phone=self.config["phone"].get(),
            code_file=self.config["code_file"].get(),
        )

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def get_account(self, id_):
        return self.browser.get_account(id_)

    def iter_history(self, account):
        return self.browser.iter_history(account)
