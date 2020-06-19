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
from datetime import date

from weboob.browser import LoginBrowser, URL, need_login, StatesMixin
from weboob.exceptions import (
    BrowserIncorrectPassword, BrowserUnavailable, ImageCaptchaQuestion, BrowserQuestion,
    WrongCaptchaResponse, AuthMethodNotImplemented, NeedInteractiveFor2FA,
)
from weboob.tools.value import Value
from weboob.browser.browsers import ClientError

from .pages import (
    LoginPage, SubscriptionsPage, DocumentsPage, DownloadDocumentPage, HomePage,
    PanelPage, SecurityPage, LanguagePage, HistoryPage,
)


class AmazonBrowser(LoginBrowser, StatesMixin):
    BASEURL = 'https://www.amazon.fr'
    CURRENCY = 'EUR'
    LANGUAGE = 'fr-FR'

    L_SIGNIN = 'Identifiez-vous'
    L_LOGIN = 'Connexion'
    L_SUBSCRIBER = 'Nom : (.*) Modifier E-mail'

    WRONGPASS_MESSAGES = [
        "Votre mot de passe est incorrect",
        "Saisissez une adresse e-mail ou un numéro de téléphone portable valable",
        "Impossible de trouver un compte correspondant à cette adresse e-mail"
    ]
    WRONG_CAPTCHA_RESPONSE = "Saisissez les caractères tels qu'ils apparaissent sur l'image."

    login = URL(r'/ap/signin(.*)', LoginPage)
    home = URL(r'/$', r'/\?language=\w+$', HomePage)
    panel = URL('/gp/css/homepage.html/ref=nav_youraccount_ya', PanelPage)
    subscriptions = URL(r'/ap/cnep(.*)', SubscriptionsPage)
    documents = URL(r'/gp/your-account/order-history\?opt=ab&digitalOrders=1(.*)&orderFilter=year-(?P<year>.*)',
                    r'https://www.amazon.fr/gp/your-account/order-history',
                    DocumentsPage)
    download_doc = URL(r'/gp/shared-cs/ajax/invoice/invoice.html', DownloadDocumentPage)
    security = URL('/ap/dcq',
                   '/ap/cvf/',
                   '/ap/mfa',
                   SecurityPage)
    language = URL(r'/gp/customer-preferences/save-settings/ref=icp_lop_(?P<language>.*)_tn', LanguagePage)
    history = URL(r'/gp/your-account/order-history\?ref_=ya_d_c_yo', HistoryPage)

    __states__ = ('otp_form', 'otp_url', 'otp_style', 'otp_headers')

    STATE_DURATION = 10

    otp_form = None
    otp_url = None
    otp_style = None
    otp_headers = None

    def __init__(self, config, *args, **kwargs):
        self.config = config
        kwargs['username'] = self.config['email'].get()
        kwargs['password'] = self.config['password'].get()
        super(AmazonBrowser, self).__init__(*args, **kwargs)

    def locate_browser(self, state):
        if '/ap/cvf/verify' not in state['url']:
            # don't perform a GET to this url, it's the otp url, which will be reached by otp_form
            self.location(state['url'])

    def push_security_otp(self, pin_code):
        res_form = self.otp_form
        res_form['rememberDevice'] = ""

        if self.otp_style == 'amazonDFA':
            res_form['code'] = pin_code
            self.location(self.otp_url, data=res_form, headers=self.otp_headers)
        else:
            res_form['otpCode'] = pin_code
            self.location('/ap/signin', data=res_form, headers=self.otp_headers)

    def handle_security(self):
        if self.config['captcha_response'].get():
            self.page.resolve_captcha(self.config['captcha_response'].get())
            # many captcha, reset value
            self.config['captcha_response'] = Value(value=None)
        else:
            otp_type = self.page.get_otp_type()
            if otp_type == '/ap/signin':
                # this otp will be always present until user deactivate it
                raise AuthMethodNotImplemented('Connection with OTP for every login is not handled')

            if self.page.has_form_verify():
                if self.config['request_information'].get() is None:
                    raise NeedInteractiveFor2FA()

                self.page.send_code()

                captcha = self.page.get_captcha_url()
                if captcha and not self.config['captcha_response'].get():
                    image = self.open(captcha).content
                    raise ImageCaptchaQuestion(image)

        if self.page.has_form_verify():
            form = self.page.get_response_form()
            self.otp_form = form['form']
            self.otp_url = self.url
            self.otp_style = form['style']
            self.otp_headers = dict(self.session.headers)

            raise BrowserQuestion(Value('pin_code', label=self.page.get_otp_message() if self.page.get_otp_message() else 'Please type the OTP you received'))

    def handle_captcha(self, captcha):
        self.otp_form = self.page.get_response_form()
        self.otp_url = self.url
        image = self.open(captcha[0]).content
        raise ImageCaptchaQuestion(image)

    def do_login(self):
        if self.config['pin_code'].get():
            # Resolve pin_code
            self.push_security_otp(self.config['pin_code'].get())

            if self.security.is_here() or self.login.is_here():
                # Something went wrong, probably a wrong OTP code
                raise BrowserIncorrectPassword('OTP incorrect')
            else:
                # Means security was passed, we're logged
                return

        if self.security.is_here():
            self.handle_security()

        if self.config['captcha_response'].get():
            # Resolve captcha code
            self.page.login(self.username, self.password, self.config['captcha_response'].get())
            # many captcha reset value
            self.config['captcha_response'] = Value(value=None)

            if self.security.is_here():
                # Raise security management
                self.handle_security()

            if self.login.is_here():
                msg = self.page.get_error_message()

                if any(wrongpass_message in msg for wrongpass_message in self.WRONGPASS_MESSAGES):
                    raise BrowserIncorrectPassword(msg)
                elif self.WRONG_CAPTCHA_RESPONSE in msg:
                    raise WrongCaptchaResponse(msg)
                else:
                    assert False, msg
            else:
                return

        # Change language so everything is handled the same way
        self.to_english(self.LANGUAGE)

        # To see if we're connected. If not, we land on LoginPage
        try:
            self.history.go()
        except ClientError:
            pass

        if not self.login.is_here():
            return

        self.page.login(self.username, self.password)

        if self.security.is_here():
            # Raise security management
            self.handle_security()

        if self.login.is_here():
            captcha = self.page.has_captcha()
            if captcha and not self.config['captcha_response'].get():
                self.handle_captcha(captcha)
            else:
                msg = self.page.get_error_message()
                assert any(wrongpass_message in msg for wrongpass_message in self.WRONGPASS_MESSAGES), msg
                raise BrowserIncorrectPassword(msg)

    def is_login(self):
        if self.login.is_here():
            self.do_login()
        else:
            raise BrowserUnavailable()

    def to_english(self, language):
        # We put language in english
        datas = {
            '_url': '/?language=' + language.replace('-', '_'),
            'LOP': language.replace('-', '_'),
        }
        self.language.go(method='POST', data=datas, language=language)

    @need_login
    def iter_subscription(self):
        self.location(self.panel.go().get_sub_link())

        if self.home.is_here():
            if self.page.get_login_link():
                self.is_login()
            self.location(self.page.get_panel_link())
        elif not self.subscriptions.is_here():
            self.is_login()

        yield self.page.get_item()

    @need_login
    def iter_documents(self, subscription):
        year = date.today().year
        old_year = year - 2
        while year >= old_year:
            self.documents.go(year=year)
            request_id = self.page.response.headers['x-amz-rid']
            for doc in self.page.iter_documents(subid=subscription.id, currency=self.CURRENCY, request_id=request_id):
                yield doc

            year -= 1
