# -*- coding: utf-8 -*-

# Copyright(C) 2020      olivm38
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

from weboob.capabilities.bank import CapBank

from weboob.tools.backend import Module, BackendConfig
from weboob.tools.value import Value, ValueBackendPassword, ValueTransient

from .proxy_browser import ProxyBrowser

__all__ = ['AnytimeModule']


class AnytimeModule(Module, CapBank):
    NAME = 'anytime'
    DESCRIPTION = u'Bank Anytime'
    MAINTAINER = u'olivm38'
    EMAIL = 'olivier@zron.fr'
    LICENSE = 'AGPLv3+'
    VERSION = '2.1'
    CONFIG = BackendConfig(
        Value('username', label='Username', regexp='.+'),
        ValueBackendPassword('password', label='Password'),
        ValueTransient('smscode'),
        ValueTransient('request_information')
    )

    BROWSER = ProxyBrowser
    STORAGE = {}

    def create_default_browser(self):
        return self.create_browser(self.config)


    def iter_accounts(self):
        return self.browser.get_accounts()


    def get_account(self, id):
        return self.browser.get_account(id)


    def iter_history(self, account):
        return self.browser.get_transactions()

    def deinit(self):
        Module.deinit(self)
