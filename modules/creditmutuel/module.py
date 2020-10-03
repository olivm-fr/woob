# -*- coding: utf-8 -*-

# Copyright(C) 2010-2011 Julien Veyssier
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


from __future__ import unicode_literals

import re
from decimal import Decimal

from weboob.capabilities.base import find_object, NotAvailable
from weboob.capabilities.bank import (
    CapBankTransferAddRecipient, AccountNotFound, RecipientNotFound,
    Account, TransferInvalidLabel,
)
from weboob.capabilities.wealth import CapBankWealth
from weboob.capabilities.contact import CapContact
from weboob.capabilities.profile import CapProfile
from weboob.capabilities.bill import (
    CapDocument, Subscription, SubscriptionNotFound,
    Document, DocumentNotFound, DocumentTypes,
)
from weboob.tools.backend import Module, BackendConfig
from weboob.tools.value import ValueBackendPassword, ValueTransient

from .browser import CreditMutuelBrowser


__all__ = ['CreditMutuelModule']


class CreditMutuelModule(
    Module, CapBankWealth, CapBankTransferAddRecipient, CapDocument,
    CapContact, CapProfile,
):

    NAME = 'creditmutuel'
    MAINTAINER = u'Julien Veyssier'
    EMAIL = 'julien.veyssier@aiur.fr'
    VERSION = '2.1'
    DESCRIPTION = u'Crédit Mutuel'
    LICENSE = 'LGPLv3+'
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Identifiant', masked=False),
        ValueBackendPassword('password', label='Mot de passe'),
        ValueTransient('resume'),
        ValueTransient('request_information'),
        ValueTransient('code', regexp=r'^\d{6}$'),
    )
    BROWSER = CreditMutuelBrowser

    accepted_document_types = (DocumentTypes.OTHER,)

    def create_default_browser(self):
        return self.create_browser(self.config, weboob=self.weboob)

    def iter_accounts(self):
        for account in self.browser.get_accounts_list():
            yield account

    def get_account(self, _id):
        account = self.browser.get_account(_id)
        if account:
            return account
        else:
            raise AccountNotFound()

    def iter_coming(self, account):
        for tr in self.browser.get_history(account):
            if tr._is_coming:
                yield tr
            else:
                break

    def iter_history(self, account):
        for tr in self.browser.get_history(account):
            if not tr._is_coming:
                yield tr

    def iter_investment(self, account):
        return self.browser.get_investment(account)

    def iter_market_orders(self, account):
        return self.browser.iter_market_orders(account)

    def iter_transfer_recipients(self, origin_account):
        if not self.browser.is_new_website:
            self.logger.info('On old creditmutuel website')
            raise NotImplementedError()

        if not isinstance(origin_account, Account):
            origin_account = find_object(self.iter_accounts(), id=origin_account, error=AccountNotFound)
        return self.browser.iter_recipients(origin_account)

    def new_recipient(self, recipient, **params):
        # second step of the new_recipient
        # there should be a parameter
        if any(p in params for p in ('Bic', 'code', 'Clé', 'resume')):
            return self.browser.set_new_recipient(recipient, **params)

        return self.browser.new_recipient(recipient, **params)

    def init_transfer(self, transfer, **params):
        if {'Clé', 'resume'} & set(params.keys()):
            return self.browser.continue_transfer(transfer, **params)

        # There is a check on the website, transfer can't be done with too long reason.
        if transfer.label:
            transfer.label = transfer.label[:27]
            # Doing a full match with (?:<regex>)\Z, re.fullmatch works only
            # for python >=3.4.
            # re.UNICODE is needed to match letters with accents in python 2 only.
            regex = r"[-\w'/=:€?!.,() ]+"
            if not re.match(r"(?:%s)\Z" % regex, transfer.label, re.UNICODE):
                invalid_chars = re.sub(regex, '', transfer.label, flags=re.UNICODE)
                raise TransferInvalidLabel("Le libellé de votre transfert contient des caractères non autorisés : %s" % invalid_chars)

        self.logger.info('Going to do a new transfer')
        if transfer.account_iban:
            account = find_object(self.iter_accounts(), iban=transfer.account_iban, error=AccountNotFound)
        else:
            account = find_object(self.iter_accounts(), id=transfer.account_id, error=AccountNotFound)

        if transfer.recipient_iban:
            recipient = find_object(self.iter_transfer_recipients(account.id), iban=transfer.recipient_iban, error=RecipientNotFound)
        else:
            recipient = find_object(self.iter_transfer_recipients(account.id), id=transfer.recipient_id, error=RecipientNotFound)

        assert account.id.isdigit(), 'Account id is invalid'

        # quantize to show 2 decimals.
        transfer.amount = Decimal(transfer.amount).quantize(Decimal(10) ** -2)

        # drop characters that can crash website
        transfer.label = transfer.label.encode('cp1252', errors="ignore").decode('cp1252')

        return self.browser.init_transfer(transfer, account, recipient)

    def execute_transfer(self, transfer, **params):
        return self.browser.execute_transfer(transfer)

    def iter_contacts(self):
        return self.browser.get_advisor()

    def get_profile(self):
        if not hasattr(self.browser, 'get_profile'):
            raise NotImplementedError()
        return self.browser.get_profile()

    def get_document(self, _id):
        subscription_id = _id.split('_')[0]
        subscription = self.get_subscription(subscription_id)
        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def get_subscription(self, _id):
        return find_object(self.iter_subscription(), id=_id, error=SubscriptionNotFound)

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)

        return self.browser.iter_documents(subscription)

    def iter_subscription(self):
        return self.browser.iter_subscriptions()

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        if document.url is NotAvailable:
            return

        return self.browser.open(document.url).content

    def iter_resources(self, objs, split_path):
        if Account in objs:
            self._restrict_level(split_path)
            return self.iter_accounts()
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()

    def iter_emitters(self):
        return self.browser.iter_emitters()
