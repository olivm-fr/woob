# -*- coding: utf-8 -*-

# Copyright(C) 2012 Kevin Pouget
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

from woob.capabilities.bank import CapBankTransferAddRecipient
from woob.capabilities.bill import CapDocument
from woob.capabilities.profile import CapProfile
from woob.tools.backend import AbstractModule

from .proxy_browser import ProxyBrowser


__all__ = ['CreditCooperatifModule']


class CreditCooperatifModule(AbstractModule, CapBankTransferAddRecipient, CapDocument, CapProfile):
    NAME = 'creditcooperatif'
    MAINTAINER = u'Kevin Pouget'
    EMAIL = 'weboob@kevin.pouget.me'
    VERSION = '3.3.1'
    DESCRIPTION = u'Crédit Coopératif'
    LICENSE = 'LGPLv3+'

    PARENT = 'caissedepargne'
    BROWSER = ProxyBrowser

    DEPENDENCIES = ('caissedepargne', 'linebourse')

    def create_default_browser(self):
        return self.create_browser(
            nuser=self.config['nuser'].get(),
            config=self.config,
            username=self.config['login'].get(),
            password=self.config['password'].get(),
            woob=self.woob,
        )
