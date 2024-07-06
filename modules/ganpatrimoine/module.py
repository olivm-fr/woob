# -*- coding: utf-8 -*-

# Copyright(C) 2012-2021  Budget Insight
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


from woob.capabilities.profile import CapProfile
from woob.capabilities.bank.wealth import CapBankWealth
from woob.tools.backend import Module, BackendConfig
from woob.tools.value import ValueBackendPassword, ValueTransient

from .browser import GanPatrimoineBrowser


__all__ = ['GanPatrimoineModule']


class GanPatrimoineModule(Module, CapBankWealth, CapProfile):
    NAME = 'ganpatrimoine'
    DESCRIPTION = 'Gan Patrimoine'
    MAINTAINER = 'Quentin Defenouillere'
    EMAIL = 'quentin.defenouillere@budget-insight.com'
    VERSION = '3.6'
    LICENSE = 'LGPLv3+'

    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Numéro client', masked=False),
        ValueBackendPassword('password', label="Code d'accès", regexp=r'\d{6}'),
        ValueTransient('otp_sms', regexp=r'\d{6}'),
        ValueTransient('request_information'),
    )

    BROWSER = GanPatrimoineBrowser

    def create_default_browser(self):
        return self.create_browser(
            'ganpatrimoine',
            self.config,
            self.config['login'].get(),
            self.config['password'].get(),
        )

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_history(self, account):
        return self.browser.iter_history(account)

    def iter_coming(self, account):
        return self.browser.iter_coming(account)

    def iter_investment(self, account):
        return self.browser.iter_investment(account)

    def get_profile(self):
        return self.browser.get_profile()
