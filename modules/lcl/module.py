# -*- coding: utf-8 -*-

# Copyright(C) 2010-2013  Romain Bignon, Pierre Mazière
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

# flake8: compatible

from decimal import Decimal
from functools import wraps
import re

from weboob.capabilities.bank import (
    CapBankTransferAddRecipient, AccountNotFound,
    RecipientNotFound, TransferError, Account,
)
from weboob.capabilities.wealth import CapBankWealth
from weboob.capabilities.bill import (
    CapDocument, Subscription, SubscriptionNotFound,
    Document, DocumentNotFound, DocumentTypes,
)
from weboob.capabilities.contact import CapContact
from weboob.capabilities.profile import CapProfile
from weboob.tools.backend import Module, BackendConfig
from weboob.tools.capabilities.bank.transactions import sorted_transactions
from weboob.tools.value import ValueBackendPassword, Value, ValueTransient
from weboob.capabilities.base import (
    find_object, strict_find_object, NotAvailable, empty,
)

from .browser import LCLBrowser, LCLProBrowser
from .enterprise.browser import LCLEnterpriseBrowser, LCLEspaceProBrowser


__all__ = ['LCLModule']


def only_for_websites(*cfg):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self.config['website'].get() not in cfg:
                raise NotImplementedError()

            return func(self, *args, **kwargs)

        return wrapper
    return decorator


class LCLModule(Module, CapBankWealth, CapBankTransferAddRecipient, CapContact, CapProfile, CapDocument):
    NAME = 'lcl'
    MAINTAINER = u'Romain Bignon'
    EMAIL = 'romain@weboob.org'
    VERSION = '2.1'
    DESCRIPTION = u'LCL'
    LICENSE = 'LGPLv3+'
    CONFIG = BackendConfig(
        ValueBackendPassword('login', label='Identifiant', masked=False),
        ValueBackendPassword('password', label='Code personnel'),
        Value(
            'website',
            label='Type de compte',
            default='par',
            choices={
                'par': 'Particuliers',
                'pro': 'Professionnels',
                'ent': 'Entreprises',
                'esp': 'Espace Pro',
            },
            aliases={'elcl': 'par'}
        ),
        ValueTransient('resume'),
        ValueTransient('request_information'),
        ValueTransient('code', regexp=r'^\d{6}$'),
    )
    BROWSER = LCLBrowser

    accepted_document_types = (DocumentTypes.STATEMENT, DocumentTypes.NOTICE, DocumentTypes.REPORT, DocumentTypes.OTHER)

    def create_default_browser(self):
        # assume all `website` option choices are defined here
        browsers = {
            'par': LCLBrowser,
            'pro': LCLProBrowser,
            'ent': LCLEnterpriseBrowser,
            'esp': LCLEspaceProBrowser,
        }

        website_value = self.config['website']
        self.BROWSER = browsers.get(
            website_value.get(),
            browsers[website_value.default]
        )

        return self.create_browser(
            self.config,
            self.config['login'].get(),
            self.config['password'].get()
        )

    def iter_accounts(self):
        return self.browser.get_accounts_list()

    def get_account(self, _id):
        return find_object(self.browser.get_accounts_list(), id=_id, error=AccountNotFound)

    def iter_coming(self, account):
        return self.browser.get_coming(account)

    def iter_history(self, account):
        transactions = sorted_transactions(self.browser.get_history(account))
        return transactions

    def iter_investment(self, account):
        return self.browser.get_investment(account)

    def iter_market_orders(self, account):
        return self.browser.iter_market_orders(account)

    @only_for_websites('par', 'pro', 'elcl')
    def iter_transfer_recipients(self, origin_account):
        acc_list = list(self.iter_accounts())
        if isinstance(origin_account, Account):
            account = strict_find_object(acc_list, iban=origin_account.iban)
            if not account:
                account = strict_find_object(acc_list, id=origin_account.id, error=AccountNotFound)
        else:
            account = find_object(acc_list, id=origin_account, error=AccountNotFound)

        return self.browser.iter_recipients(account)

    @only_for_websites('par', 'pro', 'elcl')
    def new_recipient(self, recipient, **params):
        # Recipient label has max 15 alphanumrical chars.
        recipient.label = ' '.join(w for w in re.sub('[^0-9a-zA-Z ]+', '', recipient.label).split())[:15].strip()
        return self.browser.new_recipient(recipient, **params)

    @only_for_websites('par', 'pro', 'elcl')
    def init_transfer(self, transfer, **params):
        # There is a check on the website, transfer can't be done with too long reason.
        if transfer.label:
            transfer.label = transfer.label[:30]

        self.logger.info('Going to do a new transfer')
        acc_list = list(self.iter_accounts())
        account = strict_find_object(acc_list, iban=transfer.account_iban)
        if not account:
            account = strict_find_object(acc_list, id=transfer.account_id, error=AccountNotFound)

        rcpt_list = list(self.iter_transfer_recipients(account.id))
        recipient = strict_find_object(rcpt_list, iban=transfer.recipient_iban)
        if not recipient:
            recipient = strict_find_object(rcpt_list, id=transfer.recipient_id, error=RecipientNotFound)

        try:
            # quantize to show 2 decimals.
            amount = Decimal(transfer.amount).quantize(Decimal(10) ** -2)
        except (AssertionError, ValueError):
            raise TransferError('something went wrong')

        return self.browser.init_transfer(account, recipient, amount, transfer.label, transfer.exec_date)

    def execute_transfer(self, transfer, **params):
        return self.browser.execute_transfer(transfer)

    def transfer_check_label(self, old, new):
        old = re.sub(r"[\(\)/<\?='!\+:#&%]", '', old).strip()
        old = old.encode('ISO8859-15', errors='replace').decode('ISO8859-15')  # latin-15
        # if no reason given, the site changes the label
        if not old and ("INTERNET-FAVEUR" in new):
            return True
        return super(LCLModule, self).transfer_check_label(old, new)

    def transfer_check_account_iban(self, old, new):
        # Some accounts' ibans cannot be found anymore on the website. But since we
        # kept the iban stored on our side, the 'old' transfer.account_iban is not
        # empty when making a transfer. When we do not find the account based on its iban,
        # we search it based on its id. So the account is valid, the iban is just empty.
        # This check allows to not have an assertion error when making a transfer from
        # an account in this situation.
        if empty(new):
            return True
        return old == new

    def transfer_check_recipient_iban(self, old, new):
        # Some recipients' ibans cannot be found anymore on the website. But since we
        # kept the iban stored on our side, the 'old' transfer.recipient_iban is not
        # empty when making a transfer. When we do not find the recipient based on its iban,
        # we search it based on its id. So the recipient is valid, the iban is just empty.
        # This check allows to not have an assertion error when making a transfer from
        # an recipient in this situation.
        # For example, this case can be encountered for internal accounts
        if empty(new):
            return True
        return old == new

    def transfer_check_account_id(self, old, new):
        # We can't verify here automatically that the account_id has not changed
        # as it might have changed early if a stet account id was provided instead
        # of the account id that we use here coming from the website.
        # The test "account_id not changed" will be performed directly inside init_transfer
        return True

    @only_for_websites('par', 'elcl', 'pro')
    def iter_contacts(self):
        return self.browser.get_advisor()

    def get_profile(self):
        if not hasattr(self.browser, 'get_profile'):
            raise NotImplementedError()

        profile = self.browser.get_profile()
        if profile:
            return profile
        raise NotImplementedError()

    @only_for_websites('par', 'elcl', 'pro')
    def get_document(self, _id):
        return find_object(self.iter_documents(None), id=_id, error=DocumentNotFound)

    @only_for_websites('par', 'elcl', 'pro')
    def get_subscription(self, _id):
        return find_object(self.iter_subscription(), id=_id, error=SubscriptionNotFound)

    @only_for_websites('par', 'elcl', 'pro')
    def iter_bills(self, subscription):
        return self.iter_documents(None)

    @only_for_websites('par', 'elcl', 'pro')
    def iter_documents(self, subscription):
        if not isinstance(subscription, Subscription):
            subscription = self.get_subscription(subscription)

        return self.browser.iter_documents(subscription)

    @only_for_websites('par', 'elcl', 'pro')
    def iter_subscription(self):
        return self.browser.iter_subscriptions()

    @only_for_websites('par', 'elcl', 'pro')
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
