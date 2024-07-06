
# flake8: compatible

# Copyright(C) 2013 Romain Bignon
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

import re

from woob.capabilities.base import find_object, empty
from woob.capabilities.bank import (
    Account, TransferInvalidLabel, CapBankTransfer, AccountNotFound,
    RecipientNotFound, RecipientInvalidLabel,
)
from woob.capabilities.bank.wealth import CapBankWealth
from woob.capabilities.profile import CapProfile
from woob.capabilities.bill import (
    CapDocument, Subscription, Document, DocumentNotFound,
    DocumentTypes,
)
from woob.tools.backend import Module, BackendConfig
from woob.tools.capabilities.bank.bank_transfer import sorted_transfers
from woob.tools.value import ValueBackendPassword, ValueTransient

from .browser import AXAAssuranceBrowser, AXABanqueBrowser
from .proxy_browser import ProxyBrowser


__all__ = ['AXABanqueModule']


class AXABanqueModule(Module, CapBankWealth, CapBankTransfer, CapDocument, CapProfile):
    NAME = 'axabanque'
    MAINTAINER = 'Romain Bignon'
    EMAIL = 'romain@weboob.org'
    VERSION = '3.6'
    DEPENDENCIES = ('allianzbanque',)
    DESCRIPTION = 'AXA Banque'
    LICENSE = 'LGPLv3+'
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Identifiant', masked=False),
        ValueBackendPassword('password', label='Code', regexp=r'\S+'),
        ValueTransient('code'),
        ValueTransient('request_information'),
    )
    BROWSER = ProxyBrowser
    accepted_document_types = (DocumentTypes.STATEMENT, DocumentTypes.OTHER)

    def create_default_browser(self):
        login = self.config['login'].get()
        if login.isdigit():
            self.BROWSER = ProxyBrowser
        else:
            self.BROWSER = AXAAssuranceBrowser
        return self.create_browser(
            self.config,
            login,
            self.config['password'].get(),
        )

    def iter_accounts(self):
        return self.browser.iter_accounts()

    def iter_investment(self, account):
        return self.browser.iter_investment(account)

    def iter_history(self, account):
        return self.browser.iter_history(account)

    def iter_coming(self, account):
        return self.browser.iter_coming(account)

    def iter_transfer_recipients(self, origin_account):
        if not isinstance(self.browser, AXABanqueBrowser):  # AxaAssuranceBrowser and AxaBourseBrowser can't have recipients
            raise NotImplementedError()
        if not isinstance(origin_account, Account):
            origin_account = self.get_account(origin_account)
        return self.browser.iter_recipients(origin_account)

    def new_recipient(self, recipient, **params):
        recipient.label = recipient.label[:24].upper()

        if not re.match(r"^[A-Z0-9/?:()\.,'+ ]+$", recipient.label):
            # This check is done here instead of checking the error return on the pages
            # because this appears after the sms otp. This allow the user to know that
            # the label is incorrect before having to enter an otp.
            # The message in the error is the exact one that is displayed on the website.
            raise RecipientInvalidLabel(
                message="Les caractères autorisés sont l'alphabet latin, les chiffres et "
                + "les caractères / - ? : ( ) . , ' + ESPACE"
            )

        return self.browser.new_recipient(recipient, **params)

    def init_transfer(self, transfer, **params):
        if not transfer.label:
            raise TransferInvalidLabel()

        self.logger.info('Going to do a new transfer')

        # origin account iban can be NotAvailable
        account = find_object(self.iter_accounts(), iban=transfer.account_iban)
        if not account:
            account = find_object(self.iter_accounts(), id=transfer.account_id, error=AccountNotFound)

        if transfer.recipient_iban:
            recipient = find_object(
                self.iter_transfer_recipients(account.id),
                iban=transfer.recipient_iban,
                error=RecipientNotFound
            )
        else:
            recipient = find_object(
                self.iter_transfer_recipients(account.id),
                id=transfer.recipient_id,
                error=RecipientNotFound
            )

        assert account.id.isdigit()
        # Only 11 first character are required to do transfer
        account.id = account.id[:11]

        return self.browser.init_transfer(account, recipient, transfer.amount, transfer.label, transfer.exec_date)

    def execute_transfer(self, transfer, **params):
        return self.browser.execute_transfer(transfer)

    def transfer_check_label(self, old, new):
        old = old.upper()
        return super(AXABanqueModule, self).transfer_check_label(old, new)

    def transfer_check_account_id(self, old, new):
        old = old[:11]
        return old == new

    def transfer_check_account_iban(self, old, new):
        # Skip origin account iban check and force origin account iban
        if empty(new) or empty(old):
            self.logger.warning(
                'Origin account iban check (%s) is not possible because iban is currently not available',
                old,
            )
            return True
        return old == new

    def iter_subscription(self):
        return self.browser.get_subscription_list()

    def get_document(self, _id):
        subid = _id.rsplit('_', 1)[0]
        subscription = self.get_subscription(subid)

        return find_object(self.iter_documents(subscription), id=_id, error=DocumentNotFound)

    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)
        return self.browser.iter_documents(subscription)

    def download_document(self, document):
        if not isinstance(document, Document):
            document = self.get_document(document)
        return self.browser.download_document(document)

    def iter_resources(self, objs, split_path):
        if Account in objs:
            self._restrict_level(split_path)
            return self.iter_accounts()
        if Subscription in objs:
            self._restrict_level(split_path)
            return self.iter_subscription()

    def get_profile(self):
        return self.browser.get_profile()

    def iter_emitters(self):
        if self.BROWSER != ProxyBrowser:
            raise NotImplementedError()
        return self.browser.iter_emitters()

    def iter_transfers(self, account=None):
        return sorted_transfers(self.browser.iter_transfers(account))
