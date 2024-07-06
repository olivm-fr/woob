# -*- coding: utf-8 -*-

# Copyright(C) 2018      Vincent A
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

from woob.browser import LoginBrowser, need_login, URL
from woob.capabilities.captcha import RecaptchaV2Question
from woob.capabilities.bank.wealth import Investment
from woob.exceptions import BrowserIncorrectPassword
from woob.browser.exceptions import ClientError

from .pages import LoginPage, HtmlLoginFragment, AccountsPage, AccountPage, InvestPage


class NaloBrowser(LoginBrowser):
    BASEURL = 'https://api.nalo.fr'

    login_page = URL(r'https://app.nalo.fr/components/auth/views/login.html', HtmlLoginFragment)
    login = URL(r'/api/v1/login', LoginPage)
    accounts = URL(r'/api/v1/projects/mine/without-details', AccountsPage)
    history = URL(r'/api/v1/projects/(?P<id>\d+)/history')
    account = URL(r'/api/v1/projects/(?P<id>\d+)', AccountPage)
    invests = URL(r'https://app.nalo.fr/scripts/data/data.json', InvestPage)

    token = None

    def __init__(self, config, *args, **kwargs):
        super().__init__(
            config['login'].get(),
            config['password'].get(),
            *args, **kwargs,
        )

        self.config = config

    def do_login(self):
        try:
            self.login_page.stay_or_go()
            captcha_response = self.config['captcha_response'].get()

            if not captcha_response:
                raise RecaptchaV2Question(
                    website_key=self.page.get_recaptcha_site_key(),
                    website_url=self.url,
                )

            data = {
                'email': self.username,
                'password': self.password,
                'userToken': False,
                'recaptcha': captcha_response,
            }

            self.login.go(json=data)
        except ClientError as e:
            message = e.response.json().get('detail', '')
            if 'Email ou mot de passe incorrect' in message:
                raise BrowserIncorrectPassword(message)
            raise AssertionError('An unexpected error occurred: %s' % message)
        self.token = self.page.get_token()

    def build_request(self, *args, **kwargs):
        if 'json' in kwargs:
            kwargs.setdefault('headers', {})['Accept'] = 'application/json'
        if self.token:
            kwargs.setdefault('headers', {})['Authorization'] = 'Token %s' % self.token
        return super(NaloBrowser, self).build_request(*args, **kwargs)

    @need_login
    def iter_accounts(self):
        self.accounts.go()
        return self.page.iter_accounts()

    @need_login
    def iter_history(self, account):
        self.history.go(id=account.id)
        return self.page.iter_history()

    @need_login
    def iter_investment(self, account):
        self.account.go(id=account.id)
        key = self.page.get_invest_key()

        self.invests.go()
        data = self.page.get_invest(*key)
        for item in data:
            inv = Investment()
            inv.code = item['isin']
            inv.label = item['name']
            inv.portfolio_share = item['share']
            inv.valuation = account.balance * inv.portfolio_share
            inv.asset_category = item['asset_type']
            yield inv
