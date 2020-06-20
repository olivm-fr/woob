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

from datetime import date, timedelta

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


class SwileBrowser(APIBrowser):
    BASEURL = 'https://customer-api.swile.co'

    def __init__(self, login, password, *args, **kwargs):
        """SwileBrowser needs login and password to fetch Swile API"""
        super(SwileBrowser, self).__init__(*args, **kwargs)
        # self.session.headers are the HTTP headers for Swile API requests
        self.session.headers['x-api-key'] = '23d842943f515b6d06a1c5b273bc3314e49c648a'
        self.session.headers['x-lunchr-platform'] = 'web'
        # self.credentials is the HTTP POST data used in self._auth()
        self.credentials = {
            'client_id': "533bf5c8dbd05ef18fd01e2bbbab3d7f69e3511dd08402862b5de63b9a238923",
            'grant_type': "password",
            'username': login,
            'password': password,
        }

    def _auth(self):
        """Authenticate to Swile API using self.credentials.
        If authentication succeeds, authorization header is set in self.headers
        and response's json payload is returned unwrapped into dictionary.
        """
        try:
            response = self.open('/oauth/token', data=self.credentials)
        except ClientError as e:
            json = e.response.json()
            if e.response.status_code == 401:
                message = json['error_description']
                raise BrowserIncorrectPassword(message)
            raise e

        self.session.headers['Authorization'] = 'Bearer ' + Dict('access_token')(response.json())
        return self.request('/api/v0/users/me')['user']

    def get_account(self):
        json = self._auth()
        account = Account(id=Dict('id')(json))
        account.number = account.id
        # weboob.capabilities.bank.BaseAccount
        account.bank_name = 'Swile'

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
        # make sure we have today's transactions
        before = date.today() + timedelta(days=1)

        for page in range(200):  # limit pagination
            response = self.open(
                'https://banking-api.swile.co/api/v0/payments_history',
                params={
                    'per': 20,
                    'before': before.isoformat(),
                    # don't pass page= param, it works but
                    # it's slower than the before= param
                },
            )
            json = response.json()
            if len(Dict('payments_history')(json)) == 0:
                break

            transaction = None
            for payment in Dict('payments_history')(json):
                if 'refunding_transaction' in payment:
                    refund = self._parse_transaction(payment['refunding_transaction'])
                    refund.type = Transaction.TYPE_CARD
                    yield refund

                transaction = self._parse_transaction(payment)
                if transaction:
                    # this is a millisecond-precise datetime (with a timezone).
                    # fortunately, the api excludes transactions occuring at the exact datetime we pass.
                    # if the page boundary is hit on transactions occurring at the same datetime, we might lose some of them though.
                    before = transaction.date

                    yield transaction

            if transaction is None:
                break
        else:
            raise Exception("that's a lot of transactions, probable infinite loop?")

    def _parse_transaction(self, payment):
        # Different types of payment
        # ORDER = order on swile website
        # LUNCHR_CARD_PAYMENT = pay in shop
        # PAYMENT = pay with swile card or/and linked bank card
        # MEAL_VOUCHER_CREDIT = refund
        transaction = Transaction()
        transaction_id = Dict('transaction_number', default=None)(payment)
        # Check if transaction_id is None or declined date exists which indicates failed transaction
        if transaction_id is None or Dict('declined_at', default=None)(payment):
            return

        # Check if transaction is only on cb card
        # if 'details' is empty we put default on '' because it's probably a
        # 'MEAL_VOUCHER_RENEWAL' or a 'MEAL_VOUCHER_EXPIRATION'
        if (
            Dict('type')(payment) != 'MEAL_VOUCHER_CREDIT'
            and len(Dict('details', default='')(payment)) == 1
            and Dict('details/0/type')(payment) == 'CREDIT_CARD'
        ):
            return

        # special case, if the payment is made from the platform with a card not linked to the swile card
        if Dict('type')(payment) == 'ORDER' and not Dict('details')(payment):
            return

        transaction.id = transaction_id
        transaction.date = DateTime(Dict('executed_at'))(payment)
        transaction.rdate = DateTime(Dict('created_at'))(payment)
        transaction.label = Dict('name')(payment)
        if Dict('type')(payment) == 'MEAL_VOUCHER_CREDIT':
            transaction.amount = CleanDecimal.US(Dict('amount/value'))(payment)
            transaction.type = Transaction.TYPE_DEPOSIT
        elif Dict('type')(payment) in ('MEAL_VOUCHER_RENEWAL', 'MEAL_VOUCHER_EXPIRATION'):
            transaction.amount = CleanDecimal.US(Dict('amount/value'))(payment)
            transaction.type = Transaction.TYPE_BANK
        else:
            transaction.amount = CleanDecimal.US(Dict('details/0/amount'))(payment)
            transaction.type = Transaction.TYPE_CARD

        return transaction
