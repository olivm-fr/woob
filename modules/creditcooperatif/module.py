# -*- coding: utf-8 -*-

# Copyright(C) 2012 Kevin Pouget
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

from weboob.capabilities.bank import CapBankTransferAddRecipient
from weboob.capabilities.profile import CapProfile
from weboob.tools.backend import AbstractModule, BackendConfig
from weboob.tools.value import ValueBackendPassword, Value, ValueTransient

from .proxy_browser import ProxyBrowser


__all__ = ['CreditCooperatifModule']


class CreditCooperatifModule(AbstractModule, CapBankTransferAddRecipient, CapProfile):
    NAME = 'creditcooperatif'
    MAINTAINER = u'Kevin Pouget'
    EMAIL = 'weboob@kevin.pouget.me'
    VERSION = '2.1'
    DESCRIPTION = u'Crédit Coopératif'
    LICENSE = 'LGPLv3+'
    auth_type = {'particular': "Interface Particuliers",
                 'weak' : "Code confidentiel (pro)",
                 'strong': "Sesame (pro)"}
    CONFIG = BackendConfig(
        Value('auth_type', label='Type de compte', choices=auth_type, default="particular"),
        ValueBackendPassword('login', label='Code utilisateur', masked=False),
        ValueBackendPassword('password', label='Code personnel', regexp='\d+'),
        Value('nuser', label="Numéro d'utilisateur (optionnel)", regexp='\d{0,8}', default=''),
        ValueTransient('emv_otp', regexp=r'\d{8}'),
        ValueTransient('request_information'),
    )

    PARENT = 'caissedepargne'
    BROWSER = ProxyBrowser

    def create_default_browser(self):
        return self.create_browser(
            nuser=self.config['nuser'].get(),
            config=self.config,
            username=self.config['login'].get(),
            password=self.config['password'].get(),
            weboob=self.weboob,
        )
