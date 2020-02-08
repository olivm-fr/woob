# -*- coding: utf-8 -*-

# Copyright(C) 2018      Roger Philibert
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

from weboob.browser.filters.standard import (
    CleanDecimal, CleanText, DateTime, Currency,
    Format,
)
from weboob.capabilities.base import empty
from weboob.browser.filters.json import Dict
from weboob.browser.exceptions import ClientError
from weboob.exceptions import BrowserIncorrectPassword

from weboob.browser.browsers import APIBrowser
from weboob.capabilities.bank import Account, Transaction


class LunchrBrowser(APIBrowser):
    BASEURL = 'https://api.lunchr.fr'

    def __init__(self, login, password, *args, **kwargs):
        """LunchrBrowser needs login and password to fetch Lunchr API"""
        super(LunchrBrowser, self).__init__(*args, **kwargs)
        # self.session.headers are the HTTP headers for Lunchr API requests
        self.session.headers['x-api-key'] = '644a4ef497286a229aaf8205c2dc12a9086310a8'
        self.session.headers['x-lunchr-app-version'] = 'b6c6ca66c79ca059222779fe8f1ac98c8485b9f0'
        self.session.headers['x-lunchr-platform'] = 'web'
        # self.credentials is the HTTP POST data used in self._auth()
        self.credentials = {
            'user': {
                'email': login,
                'password': password,
            }
        }

    def _auth(self):
        """Authenticate to Lunchr API using self.credentials.
        If authentication succeeds, authorization header is set in self.headers
        and response's json payload is returned unwrapped into dictionary.
        """
        try:
            response = self.open('/api/v0/users/login', data=self.credentials)
        except ClientError as e:
            json = e.response.json()
            if e.response.status_code == 401:
                message = json['result']['error']['message']
                raise BrowserIncorrectPassword(message)
            raise e
        json = Dict('user')(response.json())
        self.session.headers['Authorization'] = 'Bearer ' + Dict('token')(json)
        return json

    def get_account(self):
        json = self._auth()
        account = Account(id=Dict('id')(json))
        account.number = account.id
        # weboob.capabilities.bank.BaseAccount
        account.bank_name = 'Lunchr'

        account.type = Account.TYPE_CHECKING

        # Check if account have a card
        balance = Dict('meal_voucher_info/balance/value', default=None)(json)
        if empty(balance):
            return

        account.balance = CleanDecimal.SI(balance)(json)
        account.label = Format('%s %s', CleanText(Dict('first_name')), CleanText(Dict('last_name')))(json)
        account.currency = Currency(Dict('meal_voucher_info/balance/currency/iso_3'))(json)
        account.cardlimit = CleanDecimal.SI(Dict('meal_voucher_info/daily_balance/value'))(json)
        yield account

    def iter_history(self, account):
        page = 0
        while True:
            response = self.open('/api/v0/payments_history?page={:d}&per=20'.format(page))
            json = response.json()
            if len(Dict('payments_history')(json)) == 0:
                break

            for payment in Dict('payments_history')(json):
                if 'refunding_transaction' in payment:
                    refund = self._parse_transaction(payment['refunding_transaction'])
                    refund.type = Transaction.TYPE_CARD
                    yield refund

                transaction = self._parse_transaction(payment)
                if transaction:
                    yield transaction

            page += 1
            if page >= Dict('pagination/pages_count')(json):
                break

    def _parse_transaction(self, payment):
        transaction = Transaction()
        transaction_id = Dict('transaction_number', default=None)(payment)
        # Check if transaction_id is None which indicates failed transaction
        if transaction_id is None:
            return
        transaction.id = transaction_id
        transaction.date = DateTime(Dict('executed_at'))(payment)
        transaction.rdate = DateTime(Dict('created_at'))(payment)

        types = {
            'ORDER': Transaction.TYPE_CARD,  # order on lunchr website
            'LUNCHR_CARD_PAYMENT': Transaction.TYPE_CARD,  # pay in shop
            'MEAL_VOUCHER_CREDIT': Transaction.TYPE_DEPOSIT,
            # type can be null for refunds
        }
        transaction.type = types.get(Dict('type')(payment), Transaction.TYPE_UNKNOWN)
        transaction.label = Dict('name')(payment)
        transaction.amount = CleanDecimal(Dict('amount/value'))(payment)
        return transaction
