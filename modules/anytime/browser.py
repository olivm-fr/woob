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

import base64, re
from decimal import Decimal

from weboob.browser import URL, need_login, StatesMixin
from weboob.browser.browsers import APIBrowser, PagesBrowser
from weboob.browser.exceptions import ClientError
from weboob.capabilities.bank import Account, AccountNotFound
from weboob.capabilities.base import NotAvailable, find_object
from weboob.capabilities.bill import Subscription, Document
from weboob.exceptions import BrowserIncorrectPassword, BrowserQuestion, NeedInteractiveFor2FA
from weboob.tools.date import datetime
from weboob.tools.value import Value
from .pages import TransactionsPage, BankTransaction

__all__ = ['AnytimeBrowser']

class AnytimeBrowser(PagesBrowser):
    BASEURL = 'https://secure.anyti.me'
    TIMEOUT = 30
    
    transactions_page = URL(r'/fr/mon-compte/transactions', TransactionsPage)

    csrf_token = None

    def __init__(self, config, *args, **kwargs):
        self.config = config
        PagesBrowser.__init__(self, *args, **kwargs)

    def request(self, *args, **kwargs):
        #print(self.session.cookies)
        if self.csrf_token is None:
            self.csrf_token = self.session.cookies.get('csrf_token')
        kwargs.setdefault('headers', {})['X-CSRF-Token'] = self.csrf_token
        return self.open(*args, **kwargs)

    def get_transactions(self):
        self.transactions_page.stay_or_go()
        return self.page.get_transactions()




class AnytimeApiBrowser(APIBrowser, StatesMixin):
    BASEURL = 'https://secure.anyti.me'
    TIMEOUT = 30

    #__states__ = ['csrf_token'] # TODO remove CSRF token for production, as it generates an additional 401 call at 1st try

    csrf_token = None

    tokenid = None


    def __init__(self, config, *args, **kwargs):
        self.config = config
        APIBrowser.__init__(self, *args, **kwargs)
        #TwoFactorBrowser.__init__(self, config, username='', password='')
        StatesMixin.__init__(self)

    def request(self, *args, **kwargs):
        #print(self.session.cookies)
        if not self.logged:
            if self.session.cookies.get('dsp2_auth_token') is not None:
                auth = ':'.join((self.config['username'].get(), self.config['password'].get())).strip()
                #self.session.cookies.update({'dsp2_auth_token':self.dsp2})
                kwargs.setdefault('headers', {})['Authorization'] = 'Basic ' + base64.b64encode(auth.encode('utf-8')).decode('utf-8')
        else:
            kwargs.setdefault('headers', {})['X-CSRF-Token'] = self.csrf_token
        return self.open(*args, **kwargs)

    def _get_dsp2(self):
        if self.config['request_information'].get() is None:
            raise NeedInteractiveFor2FA()

        self.csrf_token = None
        if self.config['smscode'].get() is None:
            data = {"email": self.config['username'].get(), "password": self.config['password'].get(), "nbSmsRequest": 1}
            self.session.cookies.clear()
            response = self.request(self.BASEURL + '/api/v1/customer/auth-sms', method='POST', data=data)
            self.tokenid = response.json()['tokenId']
            self.logger.warn('Vous allez recevoir un SMS sur le numéro enregistré dans le compte Anytime.')
            raise BrowserQuestion(Value('smscode', label='Veuillez entrer le code reçu par SMS'))
        else:
            data = {"email": self.config['username'].get(), "password": self.config['password'].get(), "tokenValue":self.config['smscode'].get(), "tokenId": self.tokenid}
            response = self.request(self.BASEURL + '/api/v1/customer/auth-token', method='PUT', data=data)
            if response.status_code == 204:
                self.logger.info('Using DSP2 %s to login', self.session.cookies.get('dsp2_auth_token'))
            else:
                raise BrowserIncorrectPassword(response.text)

    def do_login(self):
        if self.session.cookies.get('dsp2_auth_token') is None:
            self._get_dsp2()
        # 11nov2022 : API has changed. It needs a form-encoded login/pwd, and is not called for a successful SMS-auth
        try:
            data = {"login": self.config['username'].get(), "password": self.config['password'].get()}
            response = self.request(self.BASEURL + '/api/v1/session', method='POST', data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        except ClientError as ex:
            if ex.response.status_code == 401:
                json_response = ex.response.json()
                if '[Unknown token]' in json_response.get('message'):
                    self.logger.warn('DSP2 token invalid, refreshing')
                    self._get_dsp2() # raises an Exception
                elif 'Authentication failed' in json_response.get('message'):
                    raise BrowserIncorrectPassword(json_response.get('message'))
            raise
        self.csrf_token = response.headers.get('X-CSRF-Token')
        #self.token_expire = (datetime.now() + timedelta(seconds=expires_in)).strftime('%Y-%m-%d %H:%M:%S')

    @property
    def logged(self):
        return self.csrf_token is not None #token_expire and datetime.strptime(self.token_expire, '%Y-%m-%d %H:%M:%S') > datetime.now()

    @need_login
    def get_accounts(self):
        yield self.get_main_account()
        response = self.request(self.BASEURL + '/api/v1/customer/cards', method='GET').json() #?filter=plastic ou filter=all , status=activated
        for card in response['cards']:
            yield self._parse_card(card)
        # single card detail available here : https://secure.anyti.me/api/v1/customer/card/ANYxxxxxxxxx

    @need_login
    def get_main_account(self):
        try:
            response = self.request(self.BASEURL + '/api/v1/customer/accounts', method='GET').json()
        except ClientError:
            self.csrf_token = None
            raise

        a = Account()
        a.type = Account.TYPE_CHECKING
        a.label = u'Checking-account-' + response[0]["name"]
        a.id = response[0]["id"]
        a.number = NotAvailable
        a.balance = Decimal(str(response[0]["amount"]))
        a.iban = response[0]["iban"]
        a.currency = response[0]["currency"]
        return a

    @staticmethod
    def _parse_card(card):
        a = Account()
        a.type = Account.TYPE_CARD
        a.label = u'Card-' + card["type"] + '-' + card["name"]
        a.id = card["reference"]
        a.number = card["pan"]
        a.balance = Decimal(str(card["balance"]))
        a.currency = card["currency"]
        return a

    @need_login
    def get_account(self, _id):
        return find_object(self.get_accounts(), id=_id, error=AccountNotFound)

    def _get_paginated(self, *args, **kwargs):
        kwargs.setdefault('params', {})['limitNumber'] = 50
        kwargs.setdefault('params', {})['limitOffset'] = 0

        while True:
            response = self.request(*args, **kwargs).json()
            for t in response['transactions']:
                yield t
            if not response['hasMoreTransactions']:  # see also 'totalTransactions'
                break
            kwargs['params']['limitOffset'] += kwargs['params']['limitNumber']

    @need_login
    def get_transactions(self, account):
        if account.type == Account.TYPE_CHECKING:
            # portal v1:
            #self.session.cookies.update({'csrf_token': self.csrf_token})
            #raise SiteSwitch('html')
            # portal v2, nov 2020 :
            response = self._get_paginated(self.BASEURL + '/api/v1/customer/corp-accounts/%s/transactions' % account.id.replace('corp-', ''), method='GET')
            for t in response:
                trans = self._parse_transaction(t, account.id)
                if trans is not None: yield trans
        elif account.type == Account.TYPE_CARD:
            response = self._get_paginated(self.BASEURL + '/api/v1/customer/cards/transactions', method='GET')
            for t in response:
                trans = self._parse_transaction(t, account.id)
                if trans is not None: yield trans

    def _parse_transaction(self, trans, acc_id):
        # id, icon, canAddFiles, nbFiles, files, isCashTx, currency : ignored
        t = BankTransaction()
        if trans['isFailed']:
            self.logger.info("Transaction failed, ignored : %s", str(trans))
            return None
        if trans['isExpired']:
            self.logger.warn("Transaction expired, ignored : %s", str(trans))
            return None
        if 'card' in trans:
            if acc_id.replace('@anytime', '') != trans['card']['ref']:
                return None
        # else: see account.acc_id et account.name
        date = datetime.fromisoformat(trans['date'])
        t.parse(
            date,
            re.sub(r'[ ]+', ' ', ' '.join([s for s in [trans['description'], trans['comment'], trans['note']] if s is not None and len(s) > 0])),
            vdate=date)
        t.set_amount(re.sub(r'[.]', ',', str(trans['amount'])))
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
        url = document.url + '&csrf=' + self.csrf_token
        response = self.request(url, method='GET')
        document.label = re.sub(r'.*filename="([^"]*)"', '\\1', response.headers['content-disposition'])
        return response.content
