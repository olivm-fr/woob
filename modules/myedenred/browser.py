# -*- coding: utf-8 -*-

# Copyright(C) 2017      Théo Dorée
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

from random import randint
from hashlib import sha256
from base64 import b64encode

from weboob.browser import LoginBrowser, URL, need_login
from weboob.exceptions import BrowserIncorrectPassword, NocaptchaQuestion
from weboob.browser.exceptions import ServerError

from .pages import (
    LoginPage, AccountsPage, TransactionsPage, InitLoginPage, TokenPage,
    ConnectCodePage, JsParamsPage, JsUserPage, JsAppPage, HomePage,
)


class MyedenredBrowser(LoginBrowser):
    BASEURL = 'https://app-container.eu.edenred.io'

    home = URL(r'https://myedenred.fr/$', HomePage)
    init_login = URL(r'https://sso.auth.api.edenred.com/idsrv/connect/authorize', InitLoginPage)
    login = URL(r'https://sso.auth.api.edenred.com/idsrv/login', LoginPage)
    connect_code = URL(r'https://www.myedenred.fr/connect', ConnectCodePage)
    token = URL(r'https://sso.auth.api.edenred.com/idsrv/connect/token', TokenPage)
    accounts = URL(r'/v1/users/(?P<username>.+)/cards', AccountsPage)
    transactions = URL(
        r'/v1/users/(?P<username>.+)/accounts/(?P<card_class>.*)-(?P<account_ref>\d+)/operations',
        TransactionsPage,
    )

    params_js = URL(r'https://www.myedenred.fr/js/parameters.(?P<random_str>\w+).js', JsParamsPage)
    connexion_js = URL(r'https://myedenred.fr/js/connexion.(?P<random_str>\w+).js', JsUserPage)
    app_js = URL(r'https://myedenred.fr/js/app.(?P<random_str>\w+).js', JsAppPage)

    def __init__(self, config, *args, **kwargs):
        super(MyedenredBrowser, self).__init__(config['login'].get(), config['password'].get(), *args, **kwargs)
        self.config = config

    def _b64encode(self, value):
        return b64encode(value).decode('utf-8').replace('+', '-').replace('/', '_').replace('=', '')

    def get_code_verifier(self):
        return self._b64encode(''.join([str(randint(0, 9)) for _ in range(32)]).encode('utf-8'))

    def get_code_challenge(self, verifier):
        return self._b64encode(sha256(verifier.encode('utf-8')).digest())

    def do_login(self):
        self.home.go()
        params_random_str = self.page.get_href_randomstring('parameters')
        connexion_random_str = self.page.get_href_randomstring('connexion')
        app_random_str = self.page.get_href_randomstring('app')

        self.params_js.go(random_str=params_random_str)
        js_parameters = self.page.get_json_content()

        self.connexion_js.go(random_str=connexion_random_str)
        connexion_js = self.page.get_json_content()

        code_verifier = self.get_code_verifier()
        code_challenge = self.get_code_challenge(code_verifier)

        self.init_login.go(params={
            'acr_values': 'tenant:fr-ben',
            'client_id': js_parameters['EDCId'],
            'code_challenge': code_challenge,
            'code_challenge_method': connexion_js['code_challenge_method'],
            'nonce': connexion_js['nonce'],
            'redirect_uri': 'https://www.myedenred.fr/connect',
            'response_type': connexion_js['response_type'],
            'scope': connexion_js['scope'],
            'state': '',
            'ui_locales': connexion_js['ui_locales'],
        })

        website_key = self.page.get_recaptcha_site_key()

        if not self.config['captcha_response'].get() and website_key:
            raise NocaptchaQuestion(website_key=website_key, website_url=self.url)

        json_model = self.page.get_json_model()
        self.location(
            'https://sso.auth.api.edenred.com' + json_model['loginUrl'],
            data={
                'idsrv.xsrf': json_model['antiForgery']['value'],
                'password': self.password,
                'username': self.username,
                'g-recaptcha-response': self.config['captcha_response'].get(),
            },
        )

        if self.login.is_here():
            raise BrowserIncorrectPassword()

        code = self.page.get_code()
        self.app_js.go(random_str=app_random_str)
        self.token.go(
            data={
                'client_id': js_parameters['EDCId'],
                'client_secret': js_parameters['EDCSecret'],
                'code': code,
                'code_verifier': code_verifier,
                'grant_type': 'authorization_code',
                'redirect_uri': self.connect_code.urls[0],
            },
            headers={'X-request-id': 'token'},
        )
        self.session.headers.update({
            'Authorization': 'Bearer ' + self.page.get_access_token(),
            'X-Client-Id': js_parameters['ClientId'],
            'X-Client-Secret': js_parameters['ClientSecret'],
            'X-request-id': 'edg_call',
        })

    @need_login
    def iter_accounts(self):
        self.accounts.go(username=self.username)
        return self.page.iter_accounts()

    @need_login
    def iter_history(self, account):
        page_index = 0
        # Max value, allowed by the webiste, for page_size is 50
        # Note it may crash for some requests (it seems for client with a few transactions)
        page_size = 50
        nb_transactions = page_size
        fetched_transactions = 0

        while nb_transactions == page_size:
            try:
                self.transactions.go(
                    username=self.username,
                    card_class=account._card_class,
                    account_ref=account._account_ref,
                    params={
                        'page_index': page_index,
                        'page_size': page_size,
                    }
                )
            except ServerError as e:
                # If page size is too much high the server may answer with a strange 500 containing a success json:
                # '{"meta": {"status": "failed", "messages": [{"code": 200, "level": "info", "text": "OK"}]}}'
                # We do not try to decode it to keep it simple and check its content as string
                if not (
                    e.response.status_code == 500
                    and b"200" in e.response.content
                    and b"OK" in e.response.content
                ):
                    # Not an exception because of our pagination
                    raise

                if page_size <= 2:
                    if not fetched_transactions:
                        # we were unable to fetch any transaction
                        # it does not look like a page size related problem
                        raise
                    else:
                        # now we get 500 but we have fetched transactions,
                        # so we consider we have reached the server limit
                        break

                # limit items per page and try again
                page_index *= 5
                page_size //= 5
                nb_transactions = page_size
                self.logger.info(
                    "Limiting items per page to %s because of a server crash: %r",
                    page_size,
                    e,
                )
                continue

            nb_transactions = len(self.page.doc['data'])
            for tr in self.page.iter_transactions():
                fetched_transactions += 1
                yield tr

            page_index += 1
