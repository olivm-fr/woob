# -*- coding: utf-8 -*-

# Copyright(C) 2018      Roger Philibert
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from woob.tools.backend import Module, BackendConfig
from woob.tools.value import ValueBackendPassword, ValueTransient
from woob.capabilities.bank import CapBank

from .browser import SwileBrowser

__all__ = ['SwileModule']


class SwileModule(Module, CapBank):
    NAME = 'swile'
    DESCRIPTION = 'Swile'
    MAINTAINER = 'Roger Philibert'
    EMAIL = 'roger.philibert@gmail.com'
    LICENSE = 'LGPLv3+'
    VERSION = '3.6'

    BROWSER = SwileBrowser

    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='E-mail', masked=False),
        ValueBackendPassword('password', label='Mot de passe'),
        ValueTransient('captcha_response', label='Captcha Response'),
    )

    def create_default_browser(self):
        return self.create_browser(self.config)

    def iter_accounts(self):
        return self.browser.get_account()

    def iter_history(self, account):
        return self.browser.iter_history(account)
