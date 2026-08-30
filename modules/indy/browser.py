# -*- coding: utf-8 -*-

# Copyright(C) 2020      olivm38
#
# This file is part of Woob.
#
# Woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Woob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import re
import time
from decimal import Decimal

from woob.browser import need_login, StatesMixin
from woob.browser.browsers import APIBrowser
from woob.browser.exceptions import ClientError
from woob.capabilities.bank import Account, AccountNotFound
from woob.capabilities.base import NotAvailable, find_object
from woob.capabilities.bill import Subscription, Document
from woob.exceptions import BrowserIncorrectPassword, BrowserQuestion, NeedInteractiveFor2FA
from woob.tools.date import datetime
from woob.tools.value import Value
from .pages import BankTransaction

__all__ = ['IndyApiBrowser']

class IndyApiBrowser(APIBrowser, StatesMixin):
    BASEURL = 'https://app.indy.fr'
    TURNSTILE_SITE_KEY = '0x4AAAAAAASufhULx9jgmdAJ'
    TIMEOUT = 30

    def __init__(self, config, *args, **kwargs):
        self.config = config
        APIBrowser.__init__(self, *args, **kwargs)
        StatesMixin.__init__(self)

    def request(self, *args, **kwargs):
        kwargs.setdefault('headers', {})['Authentication'] = 'Bearer ' + self.session.cookies.get('bearer_token')
        return self.open(*args, **kwargs)


    def get_turnstile_token(self):
        try:
            data = {'clientKey': self.config['captchakey'].get(),
                    'task': {
                        'type': 'TurnstileTaskProxyless',
                        'websiteURL': self.BASEURL + '/connexion',
                        'websiteKey': self.TURNSTILE_SITE_KEY
                    }}
            self.logger.debug(data)
            response = self.request(f'https://{self.config['captchaservice'].get()}/createTask', method='POST', data=data).json()
            self.logger.debug(response)
            if response['errorId'] != 0:
                raise BrowserIncorrectPassword(response)
            task = response['taskId']
            while True:
                data = {'clientKey': self.config['captchakey'].get(), "taskId": task}
                self.logger.debug(data)
                response = self.request(f'https://{self.config['captchaservice'].get()}/getTaskResult', method='POST', data=data).json()
                self.logger.debug(response)
                if response['status'] == 'ready':
                    return response['solution']['token']
                if response['errorId'] == 0 and response['status'] == 'processing':
                    time.sleep(5)
                else:
                    raise BrowserIncorrectPassword(response)
        except ClientError as ex:
            raise BrowserIncorrectPassword(ex.response.json())


    def do_login(self):
        try:
            turnstile_token = self.get_turnstile_token()
            data = {'email': self.config['username'].get(), 'password': self.config['password'].get(), 'h': False, 'turnstileToken': turnstile_token}
            if self.config['mfacode'].get():
                data['mfaVerifyPayload'] = {'type': 'email', 'emailCode': self.config['mfacode'].get()}
            self.logger.debug(data)
            response = self.request('/api/auth/login', method='POST', data=data).json()
            self.logger.debug(response)
            if not response['ok']:
                raise BrowserIncorrectPassword(response)
            self.session.cookies.update({'bearer_token': response['token']})
        except ClientError as ex:
            response = ex.response.json()
            self.logger.debug(response)
            if 'code' in response and response['code'] == 'authentication.mfaRequired':
                if self.config['request_information'].get() is None:
                    raise NeedInteractiveFor2FA()
                self.session.cookies.clear()
                self.logger.warning('Vous allez recevoir un email sur %s.' % self.config['username'].get())
                raise BrowserQuestion(Value('mfacode', label='Veuillez entrer le code reçu par email'))
            raise BrowserIncorrectPassword(response)

    @property
    def logged(self):
        return self.session.cookies.get('bearer_token') is not None


    @need_login
    def get_accounts(self):
        try:
            response = self.request('/api/bank-connector/bank-accounts?withAvailableBalanceInCents=false&withConnectorBankAccountStatus=true', method='GET').json()
        except ClientError:
            self.session.cookies.update('bearer_token', None)
            raise

        for r in response['bankAccounts']:
            a = Account()
            a.type = Account.TYPE_CHECKING
            a.label = r["bank"]["name"] + " - " + r["name"]
            a.id = r["_id"]
            a.number = NotAvailable
            a.balance = Decimal(str(r["balanceInCents"])) / 100  # or availableBalanceInCents ?
            a.iban = NotAvailable
            a.currency = NotAvailable
            yield a

    @need_login
    def get_account(self, _id):
        return find_object(self.get_accounts(), id=_id, error=AccountNotFound)

    def _get_paginated(self, *args, **kwargs):
        kwargs.setdefault('params', {})['page'] = 1

        nbTransactions = 0
        while True:
            response = self.request(*args, **kwargs).json()
            nbTransactions += len(response['transactions'])
            for t in response['transactions']:
                yield t
            if nbTransactions >= response['nbTransactions']:
                break
            kwargs['params']['page'] += 1

    @need_login
    def get_transactions(self, account):
        response = self._get_paginated(f'/api/transactions/transactions-list?bankAccountIds%5B%5D={account.id}', method='GET')
        for t in response:
            trans = self._parse_transaction(t)
            if trans is not None:
                yield trans

    def _parse_transaction(self, trans):
        t = BankTransaction()
        if trans['isDeleted']:
            self.logger.debug("Transaction deleted, ignored : %s", str(trans))
            return None
        date = datetime.fromisoformat(trans['date'])
        t.parse(
            date,
            trans['rawDescription'],  # re.sub(r'[ ]+', ' ', ' '.join([s for s in [trans['description'], trans['rawDescription']] if s is not None and len(s) > 0]))
            vdate=date
        )
        t.set_amount(re.sub(r'[.]', ',', str(trans['totalAmountInCents'] / 100)))
        return t

    @need_login
    def iter_subscription(self):
        for a in self.get_accounts():
            sub = Subscription()
            sub.id = '_anytime_%s' % a.id
            sub.label = 'Anytime %s' % a.id
            sub._account = a
            if a.type == Account.TYPE_CARD:
                sub.url = self.BASEURL + "/ajax-customer-pdfTransactions?what=card"
            elif a.type == Account.TYPE_CHECKING:
                sub.url = self.BASEURL + "/ajax-customer-pdfTransactions?what=corp"
            yield sub

    @need_login
    def iter_documents(self, subscription):
        if subscription._account.type == Account.TYPE_CHECKING:
            response = self.request(self.BASEURL + '/api/v1/customer/corp-accounts/%s/statements' % subscription._account.id.replace('corp-', ''), method='GET').json()
            #self.logger.debug('%s', response);
            for s in response['statements']:
                doc = Document()
                doc.date = datetime.strptime(s, '%Y-%m')
                doc.id = subscription.id + '/' + s  # s['id']
                doc.url = '%s&cid=%s&month=%s' % (subscription.url, subscription._account.id.replace('corp-', ''), s)
                doc.label = "Download the document to get the label"
                doc.format = 'pdf'
                yield doc
        elif subscription._account.type == Account.TYPE_CARD:
            response = self.request(self.BASEURL + '/api/v1/customer/card/%s/transactions' % subscription._account.id, method='GET').json()
            cid = response['cid']
            done = []
            #self.logger.debug('%s', response);
            for s in response['statements']:
                doc = Document()
                doc.date = datetime.strptime(s, '%Y-%m')
                doc.id = subscription.id + '/' + s
                doc.label = "Download the document to get the label"
                doc.format = 'pdf'
                if doc.id not in done:
                    done.append(doc.id)
                    doc.url = '%s&cid=%s&month=%s' % (subscription.url, cid, s)
                    yield doc

    @need_login
    def download_document(self, document):
        response = self.request(document.url, method='GET')
        document.label = re.sub(r'.*filename="([^"]*)"', '\\1', response.headers['content-disposition'])
        return response.content
