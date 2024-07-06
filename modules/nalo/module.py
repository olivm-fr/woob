# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
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

from woob.tools.backend import Module, BackendConfig
from woob.tools.value import ValueBackendPassword, ValueTransient
from woob.capabilities.bank.wealth import CapBankWealth

from .browser import NaloBrowser


__all__ = ['NaloModule']


class NaloModule(Module, CapBankWealth):
    NAME = 'nalo'
    DESCRIPTION = 'Nalo'
    MAINTAINER = 'Vincent A'
    EMAIL = 'dev@indigo.re'
    LICENSE = 'LGPLv3+'

    BROWSER = NaloBrowser

    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='E-mail', masked=False, regexp='.+@.+'),
        ValueBackendPassword('password', label='Mot de passe'),
        ValueTransient('captcha_response', label='Captcha Response'),
    )

    def create_default_browser(self):
        return self.create_browser(self.config)

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_investment(self, account):
        return self.browser.iter_investment(account)
