# -*- coding: utf-8 -*-

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

# flake8: compatible

from woob.capabilities.bank import Account
from woob.capabilities.bank.wealth import CapBankWealth
from woob.capabilities.base import find_object
from woob.capabilities.bill import CapDocument, Document, DocumentNotFound, DocumentTypes, Subscription
from woob.capabilities.profile import CapProfile
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import ValueBackendPassword, ValueTransient

from .browser import HSBC


__all__ = ['HSBCModule']


class HSBCModule(Module, CapBankWealth, CapProfile, CapDocument):
    NAME = 'hsbc'
    MAINTAINER = 'Romain Bignon'
    EMAIL = 'romain@weboob.org'
    VERSION = '3.6'
    LICENSE = 'LGPLv3+'
    DESCRIPTION = 'HSBC France'
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Identifiant', regexp=r'^\d{11}$', masked=False),
        ValueBackendPassword('password', label='Mot de passe'),
        ValueBackendPassword('secret', label=u'Réponse secrète'),
        ValueTransient('otp'),
        ValueTransient('request_information'),
    )
    BROWSER = HSBC

    accepted_document_types = (DocumentTypes.STATEMENT,)

    def create_default_browser(self):
        return self.create_browser(
            self.config,
            self.config['login'].get(),
            self.config['password'].get(),
            self.config['secret'].get()
        )

    def iter_resources(self, objs, split_path):
        if Account in objs:
            self._restrict_level(split_path)
            return self.iter_accounts()
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()

    def iter_accounts(self):
        for account in self.browser.iter_account_owners():
            yield account

    def iter_history(self, account):
        for tr in self.browser.get_history(account):
            yield tr

    def iter_investment(self, account):
        for tr in self.browser.get_investments(account):
            yield tr

    def iter_coming(self, account):
        for tr in self.browser.get_history(account, coming=True):
            yield tr

    def get_profile(self):
        return self.browser.get_profile()

    # CapDocument
    def iter_subscription(self):
        return self.browser.iter_subscriptions()

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def get_document(self, _id):
        subid = _id.rsplit('_', 1)[0]
        subscription = self.get_subscription(subid)
        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        return self.browser.open(document.url).content
