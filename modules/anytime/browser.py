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

import base64
from decimal import Decimal

from weboob.browser import URL, need_login, StatesMixin
from weboob.browser.browsers import APIBrowser, PagesBrowser
from weboob.browser.exceptions import ClientError
from weboob.browser.switch import SiteSwitch
from weboob.capabilities.bank import Account, AccountNotFound
from weboob.capabilities.base import NotAvailable, find_object
from weboob.exceptions import BrowserIncorrectPassword, BrowserQuestion, NeedInteractiveFor2FA
from weboob.tools.value import Value
from .pages import TransactionsPage

__all__ = ['AnytimeBrowser']

class AnytimeBrowser(PagesBrowser):
    BASEURL = 'https://secure.anyti.me'
    
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

    __states__ = ('csrf_token') # TODO remove CSRF token for production, as it generates an additional 403 call at 1st try
    csrf_token = None

    tokenid = None
    #twofa_logged_date = None


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

        if self.config['smscode'].get() is None:
            data = {"email": self.config['username'].get(), "password": self.config['password'].get()}
            self.session.cookies.clear()
            response = self.request(self.BASEURL + '/api/v1/customer/auth-sms', method='POST', data=data)
            self.tokenid = response.json()['tokenId']
            raise BrowserQuestion(Value('smscode', label='Veuillez entrer le code reçu par SMS'))
        else:
            data = {"email": self.config['username'].get(), "password": self.config['password'].get(), "tokenValue":self.config['smscode'].get(), "tokenId": self.tokenid}
            response = self.request(self.BASEURL + '/api/v1/customer/auth-token', method='PUT', data=data)
            self.logger.info('Using DSP2 %s to login', self.session.cookies.get('dsp2_auth_token'))


    def do_login(self):
        if self.session.cookies.get('dsp2_auth_token') is None:
            self._get_dsp2()

        try:
            response = self.request(self.BASEURL + '/api/v1/session', method='PUT')
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
    #raise NeedInteractiveFor2FA()

    @property
    def logged(self):
        return self.csrf_token is not None #token_expire and datetime.strptime(self.token_expire, '%Y-%m-%d %H:%M:%S') > datetime.now()

    @need_login
    def get_accounts(self):
        try:
            response = self.request(self.BASEURL + '/api/v1/customer/accounts', method='GET').json()
        except ClientError as ex:
            self.csrf_token = None
            raise
        self.logger.debug(response)

        a = Account()

        # Number26 only provides a checking account (as of sept 19th 2016).
        a.type = Account.TYPE_CHECKING
        a.label = u'Checking account'

        a.id = response[0]["id"]
        a.number = NotAvailable
        a.balance = Decimal(str(response[0]["amount"]))
        a.iban = response[0]["iban"]
        a.currency = response[0]["currency"]

        return [a]

    @need_login
    def get_account(self, _id):
        return find_object(self.get_accounts(), id=_id, error=AccountNotFound)

    @staticmethod
    def is_past_transaction(t):
        return "userAccepted" in t or "confirmed" in t

    @need_login
    def get_transactions(self):
        self.session.cookies.update({'csrf_token': self.csrf_token})
        raise SiteSwitch('html')
