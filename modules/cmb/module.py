# -*- coding: utf-8 -*-

# Copyright(C) 2012-2013 Romain Bignon
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


from weboob.tools.backend import AbstractModule, BackendConfig
from weboob.capabilities.bank import CapBankTransfer
from weboob.capabilities.contact import CapContact
from weboob.tools.value import Value, ValueBackendPassword, ValueTransient

from .par.browser import CmbParBrowser
from .pro.browser import CmbProBrowser


__all__ = ['CmbModule']


class CmbModule(AbstractModule, CapBankTransfer, CapContact):
    NAME = 'cmb'
    MAINTAINER = u'Edouard Lambert'
    EMAIL = 'elambert@budget-insight.com'
    VERSION = '2.1'
    DESCRIPTION = u'Crédit Mutuel de Bretagne'
    LICENSE = 'LGPLv3+'
    PARENT = 'cmso'
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Identifiant', masked=False),
        ValueBackendPassword('password', label='Mot de passe'),
        ValueTransient('code'),
        ValueTransient('request_information'),
        Value('website', label='Type de compte', default='par',
              choices={'par': 'Particuliers', 'pro': 'Professionnels'})
    )
    AVAILABLE_BROWSERS = {'par': CmbParBrowser, 'pro': CmbProBrowser}

    CONFIG = BackendConfig(ValueBackendPassword('login',    label='Identifiant', masked=False),
                           ValueBackendPassword('password', label='Mot de passe'),
                           ValueTransient('code'),
                           Value('website', label='Type de compte', default='par',
                                 choices={'par': 'Particuliers', 'pro': 'Professionnels'}))
