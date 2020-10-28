# -*- coding: utf-8 -*-

# Copyright(C) 2012 Romain Bignon
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

from __future__ import unicode_literals

import time
import re
import datetime
from hashlib import sha256
from uuid import uuid4
from collections import OrderedDict
from decimal import Decimal
import sys

from dateutil import parser

from weboob.browser import LoginBrowser, need_login, StatesMixin
from weboob.browser.switch import SiteSwitch
from weboob.browser.url import URL
from weboob.capabilities.bank import (
    Account, AddRecipientStep, Recipient, TransferBankError, Transaction, TransferStep,
    AddRecipientBankError,
)
from weboob.capabilities.base import NotAvailable, find_object
from weboob.capabilities.bill import Subscription
from weboob.capabilities.profile import Profile
from weboob.browser.exceptions import BrowserHTTPNotFound, ClientError, ServerError
from weboob.exceptions import (
    BrowserIncorrectPassword, BrowserUnavailable, BrowserHTTPError, BrowserPasswordExpired,
    AuthMethodNotImplemented, AppValidation, AppValidationExpired,
)
from weboob.tools.capabilities.bank.transactions import (
    sorted_transactions, FrenchTransaction, keep_only_card_transactions,
    omit_deferred_transactions,
)
from weboob.tools.capabilities.bank.investments import create_french_liquidity
from weboob.tools.compat import urljoin, urlparse, parse_qsl, parse_qs, urlencode, urlunparse
from weboob.tools.date import date
from weboob.tools.json import json
from weboob.tools.value import Value
from weboob.tools.decorators import retry

from .pages import (
    IndexPage, ErrorPage, MarketPage, LifeInsurance, LifeInsuranceHistory, LifeInsuranceInvestments,
    GarbagePage, MessagePage, LoginPage,
    TransferPage, ProTransferPage, TransferConfirmPage, TransferSummaryPage, ProTransferConfirmPage,
    ProTransferSummaryPage, ProAddRecipientOtpPage, ProAddRecipientPage,
    SmsPage, ValidationPageOption, AuthentPage, RecipientPage, CanceledAuth,
    CaissedepargneKeyboard, CaissedepargneNewKeyboard,
    TransactionsDetailsPage, LoadingPage, ConsLoanPage, MeasurePage,
    NatixisLIHis, NatixisLIInv, NatixisRedirectPage,
    SubscriptionPage, CreditCooperatifMarketPage, UnavailablePage,
    CardsPage, CardsComingPage, CardsOldWebsitePage, TransactionPopupPage,
    OldLeviesPage, NewLeviesPage, NewLoginPage, JsFilePage, AuthorizePage,
    AuthenticationMethodPage, VkImagePage, AuthenticationStepPage, LoginTokensPage,
    AppValidationPage,
)
from .transfer_pages import CheckingPage, TransferListPage

from .linebourse_browser import LinebourseAPIBrowser


__all__ = ['CaisseEpargne']


def decode_utf8_cookie(data):
    # caissedepargne/palatine cookies may contain non-ascii bytes which is ill-defined.
    # Actually, they use utf-8.
    # Since it's not standard, requests/urllib interprets it freely... as latin-1
    # and we can't really blame for that.
    # Let's decode this shit ourselves.
    if sys.version_info.major == 2 and isinstance(data, bytes):
        # on top of that, sometimes the cookie is already unicode
        # which part does this? urllib? requests?
        # who knows, in the end we have to avoid puking despite the stench
        return data.decode('utf-8')
    else:
        return data.encode('latin-1').decode('utf-8')


def monkeypatch_for_lowercase_percent(session):
    # In the transfer flow, the main site (something like net123.caisse-epargne.fr)
    # redirects to the OTP site (something like www.icgauth.caisse-epargne.fr).
    # %2F is equivalent to %2f, right? It's hexadecimal after all. That's what
    # RFC3986, RFC2396, RFC1630 say, also normalization of case is possible.
    # That's what requests and urllib3 implement.
    # But some dumbasses think otherwise and simply violate the RFCs.
    # They SHOULD [interpreted as described in RFC2119] step away from the computer
    # and never touch it again because they are obviously too stupid to use it.
    # So, we are forced to hack deep in urllib3 to force our custom URL tweaking.

    def patch_attr(obj, attr, func):
        if hasattr(obj, '_old_%s' % attr):
            return

        old_func = getattr(obj, attr)
        setattr(obj, '_old_%s' % attr, old_func)
        setattr(obj, attr, func)

    pm = session.adapters['https://'].poolmanager

    def connection_from_host(*args, **kwargs):
        pool = pm._old_connection_from_host(*args, **kwargs)

        def make_request(conn, method, url, *args, **kwargs):
            if url.startswith('/dacswebssoissuer/AuthnRequestServlet'):
                # restrict this hazardous change to otp urls
                url = re.sub(r'%[0-9A-F]{2}', lambda m: m.group(0).lower(), url)
            return pool._old__make_request(conn, method, url, *args, **kwargs)

        patch_attr(pool, '_make_request', make_request)
        return pool

    patch_attr(pm, 'connection_from_host', connection_from_host)


class CaisseEpargne(LoginBrowser, StatesMixin):
    BASEURL = "https://www.caisse-epargne.fr"
    STATE_DURATION = 5
    HISTORY_MAX_PAGE = 200
    TIMEOUT = 60

    LINEBOURSE_BROWSER = LinebourseAPIBrowser

    login = URL(
        r'/authentification/manage\?step=identification&identifiant=(?P<login>.*)',
        r'https://.*/login.aspx',
        LoginPage
    )

    new_login = URL(r'/se-connecter/sso', NewLoginPage)
    js_file = URL(r'/se-connecter/main-.*.js$', JsFilePage)

    authorize = URL(r'https://www.as-ex-ath-groupe.caisse-epargne.fr/api/oauth/v2/authorize', AuthorizePage)
    login_tokens = URL(r'https://www.as-ex-ath-groupe.caisse-epargne.fr/api/oauth/v2/consume', LoginTokensPage)

    # Login and transfer authentication
    authentication_step = URL(
        r'https://(?P<domain>www.icgauth.[^/]+)/dacsrest/api/v1u0/transaction/(?P<validation_id>[^/]+)/step',
        AuthenticationStepPage
    )
    authentication_method_page = URL(
        r'https://(?P<domain>www.icgauth.[^/]+)/dacsrest/api/v1u0/transaction/(?P<validation_id>)',
        r'https://www.icgauth.caisse-epargne.fr/dacsrest/api/v1u0/transaction/.*',
        AuthenticationMethodPage,
    )
    vk_image = URL(
        r'https://(?P<domain>www.icgauth.[^/]+)/dacs-rest-media/api/v1u0/medias/mappings/[a-z0-9-]+/images',
        VkImagePage,
    )

    # eg of both possible regexes:
    # https://www.icgauth.caisse-epargne.fr/dacstemplate-SOL/index.html?transactionID=CtxDACSP[a-f0-9]+
    # https://www.icgauth.caisse-epargne.fr/dacstemplate-SOL/_12579/index.html?transactionID=CtxDACSP[a-f0-9]+
    validation_option = URL(
        r'https://(?P<domain>www.icgauth.[^/]+)/dacstemplate-SOL/(?:[^/]+/)?index.html\?transactionID=.*',
        ValidationPageOption
    )
    sms = URL(r'https://(?P<domain>www.icgauth.[^/]+)/dacswebssoissuer/AuthnRequestServlet', SmsPage)
    app_validation = URL(r'https://(?P<domain>www.icgauth.[^/]+)/dacsrest/WaitingCallbackHandler', AppValidationPage)

    account_login = URL(
        r'/authentification/manage\?step=account&identifiant=(?P<login>.*)&account=(?P<accountType>.*)',
        LoginPage
    )
    loading = URL(r'https://.*/CreditConso/ReroutageCreditConso.aspx', LoadingPage)
    cons_loan = URL(
        r'https://www.credit-conso-cr.caisse-epargne.fr/websavcr-web/rest/contrat/getContrat\?datePourIe=(?P<datepourie>)',
        ConsLoanPage
    )
    transaction_detail = URL(r'https://.*/Portail.aspx.*', TransactionsDetailsPage)
    recipient = URL(r'https://.*/Portail.aspx.*', RecipientPage)
    checking = URL(r'https://.*/Portail.aspx.*', CheckingPage)
    transfer_list = URL(r'https://.*/Portail.aspx.*', TransferListPage)
    transfer = URL(r'https://.*/Portail.aspx.*', TransferPage)
    transfer_summary = URL(r'https://.*/Portail.aspx.*', TransferSummaryPage)
    transfer_confirm = URL(r'https://.*/Portail.aspx.*', TransferConfirmPage)
    pro_transfer = URL(r'https://.*/Portail.aspx.*', ProTransferPage)
    pro_transfer_confirm = URL(r'https://.*/Portail.aspx.*', ProTransferConfirmPage)
    pro_transfer_summary = URL(r'https://.*/Portail.aspx.*', ProTransferSummaryPage)
    pro_add_recipient_otp = URL(r'https://.*/Portail.aspx.*', ProAddRecipientOtpPage)
    pro_add_recipient = URL(r'https://.*/Portail.aspx.*', ProAddRecipientPage)
    measure_page = URL(r'https://.*/Portail.aspx.*', MeasurePage)
    cards_old = URL(r'https://.*/Portail.aspx.*', CardsOldWebsitePage)
    cards = URL(r'https://.*/Portail.aspx.*', CardsPage)
    cards_coming = URL(r'https://.*/Portail.aspx.*', CardsComingPage)
    old_checkings_levies = URL(r'https://.*/Portail.aspx.*', OldLeviesPage)
    new_checkings_levies = URL(r'https://.*/Portail.aspx.*', NewLeviesPage)
    authent = URL(r'https://.*/Portail.aspx.*', AuthentPage)
    subscription = URL(r'https://.*/Portail.aspx\?tache=(?P<tache>).*', SubscriptionPage)
    transaction_popup = URL(r'https://.*/Portail.aspx.*', TransactionPopupPage)
    home = URL(r'https://.*/Portail.aspx.*', IndexPage)
    home_tache = URL(r'https://.*/Portail.aspx\?tache=(?P<tache>).*', IndexPage)
    error = URL(
        r'https://.*/login.aspx',
        r'https://.*/Pages/logout.aspx.*',
        r'https://.*/particuliers/Page_erreur_technique.aspx.*',
        ErrorPage
    )
    market = URL(
        r'https://.*/Pages/Bourse.*',
        r'https://www.caisse-epargne.offrebourse.com/ReroutageSJR',
        r'https://www.caisse-epargne.offrebourse.com/fr/6CE.*',
        MarketPage
    )
    unavailable_page = URL(r'https://www.caisse-epargne.fr/.*/au-quotidien', UnavailablePage)

    creditcooperatif_market = URL(r'https://www.offrebourse.com/.*', CreditCooperatifMarketPage)  # just to catch the landing page of the Credit Cooperatif's Linebourse
    natixis_redirect = URL(
        r'/NaAssuranceRedirect/NaAssuranceRedirect.aspx',
        r'https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/views/common/routage-itce.xhtml\?windowId=automatedEntryPoint',
        NatixisRedirectPage
    )
    life_insurance_history = URL(
        r'https://www.extranet2.caisse-epargne.fr/cin-front/contrats/evenements',
        LifeInsuranceHistory
    )
    life_insurance_investments = URL(
        r'https://www.extranet2.caisse-epargne.fr/cin-front/contrats/details',
        LifeInsuranceInvestments
    )
    life_insurance = URL(
        r'https://.*/Assurance/Pages/Assurance.aspx',
        r'https://www.extranet2.caisse-epargne.fr.*',
        LifeInsurance
    )
    natixis_life_ins_his = URL(
        r'https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/rest/v2/contratVie/load-operation/(?P<id1>\w+)/(?P<id2>\w+)/(?P<id3>)',
        NatixisLIHis
    )
    natixis_life_ins_inv = URL(
        r'https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/rest/v2/contratVie/load/(?P<id1>\w+)/(?P<id2>\w+)/(?P<id3>)',
        NatixisLIInv
    )
    message = URL(r'https://www.caisse-epargne.offrebourse.com/DetailMessage\?refresh=O', MessagePage)
    garbage = URL(
        r'https://www.caisse-epargne.offrebourse.com/Portefeuille',
        r'https://www.caisse-epargne.fr/particuliers/.*/emprunter.aspx',
        r'https://.*/particuliers/emprunter.*',
        r'https://.*/particuliers/epargner.*',
        GarbagePage
    )

    __states__ = (
        'BASEURL', 'multi_type', 'typeAccount', 'is_cenet_website', 'recipient_form',
        'is_send_sms', 'is_app_validation', 'otp_validation',
    )

    # Accounts managed in life insurance space (not in linebourse)

    insurance_accounts = (
        'AIKIDO',
        'ASSURECUREUIL',
        'ECUREUIL PROJET',
        'GARANTIE RETRAITE EU',
        'INITIATIVES PLUS',
        'INITIATIVES TRANSMIS',
        'LIVRET ASSURANCE VIE',
        'OCEOR EVOLUTION',
        'PATRIMONIO CRESCENTE',
        'PEP TRANSMISSION',
        'PERP',
        'PERSPECTIVES ECUREUI',
        'POINTS RETRAITE ECUR',
        'RICOCHET',
        'SOLUTION PERP',
        'TENDANCES',
        'YOGA',
    )

    def __init__(self, nuser, *args, **kwargs):
        self.BASEURL = kwargs.pop('domain', self.BASEURL)
        if not self.BASEURL.startswith('https://'):
            self.BASEURL = 'https://%s' % self.BASEURL

        self.is_cenet_website = False
        self.new_website = True
        self.multi_type = False
        self.accounts = None
        self.loans = None
        self.typeAccount = None
        self.inexttype = 0  # keep track of index in the connection type's list
        self.nuser = nuser
        self.recipient_form = None
        self.is_send_sms = None
        self.otp_validation = None
        self.weboob = kwargs['weboob']
        self.market_url = kwargs.pop(
            'market_url',
            'https://www.caisse-epargne.offrebourse.com',
        )
        self.has_subscription = True

        super(CaisseEpargne, self).__init__(*args, **kwargs)

        dirname = self.responses_dirname
        if dirname:
            dirname += '/bourse'

        self.linebourse = self.LINEBOURSE_BROWSER(
            self.market_url,
            logger=self.logger,
            responses_dirname=dirname,
            weboob=self.weboob,
            proxy=self.PROXIES,
        )

        monkeypatch_for_lowercase_percent(self.session)

    def deleteCTX(self):
        # For connection to offrebourse and natixis, we need to delete duplicate of CTX cookie
        if len([k for k in self.session.cookies.keys() if k == 'CTX']) > 1:
            del self.session.cookies['CTX']

    def load_state(self, state):
        if state.get('expire') and parser.parse(state['expire']) < datetime.datetime.now():
            return self.logger.info('State expired, not reloading it from storage')

        transfer_states = ('recipient_form', 'is_app_validation', 'is_send_sms', 'otp_validation')

        for transfer_state in transfer_states:
            if transfer_state in state and state[transfer_state] is not None:
                super(CaisseEpargne, self).load_state(state)
                self.logged = True
                break

    def locate_browser(self, state):
        # in case of transfer/add recipient, we shouldn't go back to previous page
        # site will crash else
        pass

    def do_login(self):
        data = self.get_connection_data()
        accounts_types = data.get('account')

        if data.get('authMode', '') == 'redirect':  # the connection type EU could also be used as a criteria
            raise SiteSwitch('cenet')

        type_account = data['account'][0]

        if self.multi_type:
            assert type_account == self.typeAccount

        if 'keyboard' in data:
            self.do_old_login(data, type_account, accounts_types)
        else:
            # New virtual keyboard
            self.do_new_login(data)

    def get_connection_data(self):
        """
        Attempt to log in.
        Note: this method does nothing if we are already logged in.
        """
        # Among the parameters used during the login step, there is
        # a connection type (called typeAccount) that can take the
        # following values:
        # WE: espace particulier
        # WP: espace pro
        # WM: personnes protégées
        # EU: Cenet
        #
        # A connection can have one connection type as well as many of
        # them. There is an issue when there is many connection types:
        # the connection type to use can't be guessed in advance, we
        # have to test all of them until the login step is successful
        # (sometimes all connection type can be used for the login, sometimes
        # only one will work).
        #
        # For simplicity's sake, we try each connection type from first to
        # last (they are returned in a list by the first request)
        #
        # Examples of connection types combination that have been seen so far:
        # [WE]
        # [WP]
        # [WE, WP]
        # [WE, WP, WM]
        # [WP, WM]
        # [EU]
        # [EU, WE]  (EU tends to come first when present)

        if not self.username or not self.password:
            raise BrowserIncorrectPassword()

        @retry(ValueError)
        def retry_go_login():
            """
            On occasions the page is not the expected JsonPage,
            although response is a code 200,
            and trying to parse it as such would throw a JSONDecodeError.
            Retrying does the trick and avoids raising a BrowserUnavailable.
            """
            return self.login.go(login=self.username)

        # Retrieve the list of types: can contain a single type or more
        # - when there is a single type: all the information are available
        # - when there are several types: an additional request is needed
        connection = retry_go_login()

        data = connection.get_response()
        if data is None:
            raise BrowserIncorrectPassword()

        data = self.check_connection_data(data)
        assert data is not None
        return data

    def check_connection_data(self, data):
        accounts_types = data.get('account', [])
        if not self.nuser and 'WE' not in accounts_types:
            raise BrowserIncorrectPassword("Utilisez Caisse d'Épargne Professionnels et renseignez votre nuser pour connecter vos comptes sur l'epace Professionels ou Entreprises.")

        if len(accounts_types) > 1:
            # Additional request when there is more than one connection type
            # to "choose" from the list of connection types
            self.multi_type = True

            if self.inexttype < len(accounts_types):
                if accounts_types[self.inexttype] == 'EU' and not self.nuser:
                    # when EU is present and not alone, it tends to come first
                    # if nuser is unset though, user probably doesn't want 'EU'
                    self.inexttype += 1
                elif accounts_types[self.inexttype] == 'WE' and self.nuser:
                    # User is probably a netpro user and want to access their
                    # professional accounts
                    self.inexttype += 1

                self.typeAccount = accounts_types[self.inexttype]
            else:
                raise AssertionError('should have logged in with at least one connection type')
            self.inexttype += 1

            data = self.account_login.go(login=self.username, accountType=self.typeAccount).get_response()

        return data

    def do_old_login(self, data, type_account, accounts_types):
        # Old virtual keyboard
        id_token_clavier = data['keyboard']['Id']
        vk = CaissedepargneKeyboard(data['keyboard']['ImageClavier'], data['keyboard']['Num']['string'])

        newCodeConf = vk.get_string_code(self.password)

        payload = {
            'idTokenClavier': id_token_clavier,
            'newCodeConf': newCodeConf,
            'auth_mode': 'ajax',
            'nuusager': self.nuser.encode('utf-8'),
            'codconf': '',  # must be present though empty
            'typeAccount': type_account,
            'step': 'authentification',
            'ctx': 'typsrv={}'.format(type_account),
            'clavierSecurise': '1',
            'nuabbd': self.username,
        }

        try:
            res = self.location(data['url'], params=payload)
        except ValueError:
            raise BrowserUnavailable()
        if not res.page:
            raise BrowserUnavailable()

        response = res.page.get_response()

        assert response is not None

        if response['error'] == 'Veuillez changer votre mot de passe':
            raise BrowserPasswordExpired(response['error'])

        if not response['action']:
            # the only possible way to log in w/o nuser is on WE. if we're here no need to go further.
            if not self.nuser and self.typeAccount == 'WE':
                raise BrowserIncorrectPassword(self.page.get_wrongpass_message())

            # all typeAccount tested and still not logged
            # next iteration will throw the AssertionError if we don't raise an error here
            if self.inexttype == len(accounts_types):
                raise BrowserIncorrectPassword(self.page.get_wrongpass_message())

            if self.multi_type:
                # try to log in with the next connection type's value
                self.do_login()
                return
            raise BrowserIncorrectPassword(self.page.get_wrongpass_message())

        self.BASEURL = urljoin(data['url'], '/')

        try:
            self.home.go()
        except BrowserHTTPNotFound:
            raise BrowserIncorrectPassword()

    def get_auth_mechanisms_validation_info(self):
        """ First step of strong authentication validation

        This method retrieve all informations needed for validation form.
        Warning: need to be on `validation_option` page to get the "transaction ID".
        """
        transaction_id = re.search(r'transactionID=(.*)', self.page.url)
        if transaction_id:
            transaction_id = transaction_id.group(1)
        else:
            raise AssertionError('Transfer transaction id was not found in url')

        otp_validation_domain = urlparse(self.url).netloc

        self.authentication_method_page.go(
            domain=otp_validation_domain,
            validation_id=transaction_id
        )

        # Can have error at first authentication request.
        # In that case, it's not an invalid otp error.
        # So, return a wrongpass.
        self.page.check_errors(feature='login')

        self.otp_validation = self.page.get_authentication_method_info()

        if self.otp_validation['type'] not in ('SMS', 'CLOUDCARD', 'PASSWORD'):
            self.logger.warning('Not handled authentication method : "%s"' % self.otp_validation['type'])
            raise AuthMethodNotImplemented()

        self.otp_validation['validation_unit_id'] = self.page.validation_unit_id
        self.otp_validation['validation_id'] = transaction_id
        self.otp_validation['domain'] = otp_validation_domain

    def do_otp_sms_authentication(self, **params):
        """ Second step of sms authentication validation

        This method validate otp sms.
        Warning:
        * need to be used through `do_authentication_validation` method
        in order to handle authentication response
        * do not forget to use the first part to have all form information
        * do not forget to set `otp_sms` params

        Parameters:
        otp_sms (str): the OTP received by SMS
        """
        assert self.otp_validation
        assert 'otp_sms' in params

        self.authentication_step.go(
            domain=self.otp_validation['domain'],
            validation_id=self.otp_validation['validation_id'],
            json={
                'validate': {
                    self.otp_validation['validation_unit_id']: [{
                        'id': self.otp_validation['id'],
                        'otp_sms': params['otp_sms'],
                        'type': 'SMS',
                    }],
                },
            }
        )

        self.otp_validation = None

    def do_cloudcard_authentication(self, **params):
        """ Second step of cloudcard authentication validation

        This method check the application validation status.
        Warning:
        * need to be used through `do_authentication_validation` method
        in order to handle authentication response
        * do not forget to use the first part to have all form information
        """
        assert self.otp_validation

        timeout = time.time() + 300.0
        referer_url = self.authentication_method_page.build(
            domain=self.otp_validation['domain'],
            validation_id=self.otp_validation['validation_id'],
        )

        while time.time() < timeout:
            self.app_validation.go(
                domain=self.otp_validation['domain'],
                headers={'Referer': referer_url},
            )
            status = self.page.get_status()
            # The status is 'valid' even when the user cancels it on
            # the application. The `authentication_step` will return
            # AUTHENTICATION_CANCELED in its response status.
            if status == 'valid':
                self.authentication_step.go(
                    domain=self.otp_validation['domain'],
                    validation_id=self.otp_validation['validation_id'],
                    json={
                        'validate': {
                            self.otp_validation['validation_unit_id']: [{
                                'id': self.otp_validation['id'],
                                'type': 'CLOUDCARD',
                            }],
                        },
                    },
                )
                break

            assert status == 'progress', 'Unhandled CloudCard status : "%s"' % status
            time.sleep(2)
        else:
            raise AppValidationExpired()

        self.otp_validation = None

    def do_vk_authentication(self, **params):
        """ Authentication with virtual keyboard

        Warning: need to be used through `do_authentication_validation` method
        in order to handle authentication response
        """

        # Can have error at first authentication request.
        # In that case, it's not a vk error, return a wrongpass.
        self.page.check_errors(feature='login')

        validation_id = self.page.get_validation_id()
        validation_unit_id = self.page.validation_unit_id

        vk_info = self.page.get_authentication_method_info()
        vk_id = vk_info['id']
        vk_images_url = vk_info['virtualKeyboard']['externalRestMediaApiUrl']
        otp_validation_domain = urlparse(self.url).netloc

        self.location(vk_images_url)
        images_url = self.page.get_all_images_data()
        vk = CaissedepargneNewKeyboard(self, images_url)
        code = vk.get_string_code(self.password)

        self.authentication_step.go(
            domain=otp_validation_domain,
            validation_id=validation_id,
            json={
                'validate': {
                    validation_unit_id: [{
                        'id': vk_id,
                        'password': code,
                        'type': 'PASSWORD',
                    }],
                },
            },
            headers={
                'Referer': self.BASEURL,
                'Accept': 'application/json, text/plain, */*',
            },
        )

    def do_authentication_validation(self, authentication_method, feature, **params):
        """ Handle all sort of authentication with `icgauth`

        This method is used for login or transfer/new recipient authentication.

        Parameters:
        authentication_method (str): authentication method in ('SMS', 'CLOUDCARD', 'PASSWORD')
        feature (str): action that need authentication in ('login', 'transfer', 'recipient')
        """
        AUTHENTICATION_METHODS = {
            'SMS': self.do_otp_sms_authentication,
            'CLOUDCARD': self.do_cloudcard_authentication,
            'PASSWORD': self.do_vk_authentication,
        }
        AUTHENTICATION_METHODS[authentication_method](**params)

        assert self.authentication_step.is_here()
        self.page.check_errors(feature=feature)

        redirect_data = self.page.get_redirect_data()
        assert redirect_data, 'redirect_data must not be empty'

        self.location(
            redirect_data['action'],
            data={
                'SAMLResponse': redirect_data['samlResponse'],
            },
            headers={
                'Referer': self.BASEURL,
                'Accept': 'application/json, text/plain, */*',
            },
        )

    def do_new_login(self, data):
        connection_type = self.page.get_connection_type()
        csid = str(uuid4())
        redirect_url = data['url']

        parts = list(urlparse(redirect_url))
        url_params = parse_qs(urlparse(redirect_url).query)

        qs = OrderedDict(parse_qsl(parts[4]))
        qs.update({'csid': csid})
        parts[4] = urlencode(qs)
        url = urlunparse(parts)

        continue_url = url_params['continue'][0]
        continue_parameters = data['continueParameters']

        # snid is either present in continue_parameters (creditcooperatif / banquebcp)
        # or in url_params (caissedepargne / other children)
        snid = json.loads(continue_parameters).get('snid') or url_params['snid'][0]

        self.location(
            url,
            method='POST',
            params={
                'continue_parameters': continue_parameters,
            },
        )

        main_js_file = self.page.get_main_js_file_url()
        self.location(main_js_file)

        client_id = self.page.get_client_id()
        nonce = self.page.get_nonce()  # Hardcoded in their js...

        # On the website, this sends back json because of the header
        # 'Accept': 'applcation/json'. If we do not add this header, we
        # instead have a form that we can directly send to complete
        # the login.

        claims = {
            'userinfo': {
                'cdetab': None,
                'authMethod': None,
                'authLevel': None,
            },
            'id_token': {
                'auth_time': {"essential": True},
                "last_login": None,
            },
        }
        bpcesta = {
            "csid": csid,
            "typ_app": "rest",
            "enseigne": "ce",
            "typ_sp": "out-band",
            "typ_act": "auth",
            "snid": snid,
            "cdetab": url_params['cdetab'][0],
            "typ_srv": connection_type,
        }
        params = {
            'nonce': nonce,
            'scope': 'openid readUser',
            'response_type': 'id_token token',
            'response_mode': 'form_post',
            'cdetab': url_params['cdetab'][0],
            'login_hint': self.username,
            'display': 'page',
            'client_id': client_id,
            # don't know if the separators= is really needed
            'claims': json.dumps(claims, separators=(',', ':')),
            'bpcesta': json.dumps(bpcesta, separators=(',', ':')),
        }
        if self.nuser:
            params['login_hint'] += ' %s' % self.nuser

        self.authorize.go(params=params)
        self.page.send_form()

        if self.response.headers.get('Page_Erreur', '') == 'INDISPO':
            raise BrowserUnavailable()

        pre_login_status = self.page.get_wrong_pre_login_status()
        if pre_login_status == 'AUTHENTICATION_FAILED':
            # failing at this step means no password has been submitted yet
            # and no auth method type cannot be recovered
            # corresponding to 'erreur technique' on website
            raise BrowserUnavailable()

        authentication_method = self.page.get_authentication_method_type()
        self.do_authentication_validation(
            authentication_method=authentication_method,
            feature='login'
        )

        access_token = self.page.get_access_token()
        id_token = self.page.get_id_token()

        continue_parameters = json.loads(continue_parameters)
        self.location(
            continue_url,
            data={
                'id_token': id_token,
                'access_token': access_token,
                'ctx': continue_parameters['ctx'],
                'redirectUrl': continue_parameters['redirectUrl'],
                'ctx_routage': continue_parameters['ctx_routage'],
            },
        )
        # Url look like this : https://www.net382.caisse-epargne.fr/Portail.aspx
        # We only want the https://www.net382.caisse-epargne.fr part
        # We start the .find at 8 to get the first `/` after `https://`
        parsed_url = urlparse(self.url)
        self.BASEURL = 'https://' + parsed_url.netloc

    def loans_conso(self):
        days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
        month = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
        now = datetime.datetime.today()
        # for non-DST
        # d = '%s %s %s %s %s:%s:%s GMT+0100 (heure normale d’Europe centrale)' % (days[now.weekday()], now.day, month[now.month - 1], now.year, now.hour, format(now.minute, "02"), now.second)
        # TODO use babel library to simplify this code
        d = '%s %s %s %s %s:%s:%s GMT+0200 (heure d’été d’Europe centrale)' % (
            days[now.weekday()], now.day, month[now.month - 1], now.year,
            now.hour, format(now.minute, "02"), now.second,
        )
        if self.home.is_here():
            msg = self.page.loan_unavailable_msg()
            if msg:
                self.logger.warning('%s' % msg)
                return None
        self.cons_loan.go(datepourie=d)
        return self.page.get_conso()

    def go_measure_list(self, page_num=0):
        self.home.go()

        if not self.measure_page.is_here():
            raise AssertionError('Should be on measure_page')

        self.page.go_measure_list()
        for _ in range(page_num):
            self.page.goto_next_page()

    def get_owner_name(self):
        # Get name from profile to verify who is the owner of accounts.
        name = self.get_profile().name.upper().split(' ', 1)
        if len(name) == 2:  # if the name is complete (with first and last name)
            owner_name = name[1]
        else:  # if there is only first name
            owner_name = name[0]
        return owner_name

    @need_login
    def get_measure_accounts_list(self):
        """
        On home page there is a list of "measure" links, each one leading to one person accounts list.
        Iter over each 'measure' and navigate to it to get all accounts
        """
        self.home.go()

        owner_name = self.get_owner_name()
        # Make sure we are on list of measures page
        if self.measure_page.is_here():
            self.page.check_no_accounts()
            self.accounts = []
            for page_num in range(20):
                for measure_id in self.page.get_measure_ids():
                    self.page.go_measure_accounts_list(measure_id)
                    if self.page.check_measure_accounts():
                        for account in self.page.get_list(owner_name):
                            account._info['measure_id'] = measure_id
                            account._info['measure_id_page_num'] = page_num
                            self.accounts.append(account)
                    self.go_measure_list(page_num)
                if not self.page.has_next_page():
                    break
                self.page.goto_next_page()

            for account in self.accounts:
                if 'acc_type' in account._info and account._info['acc_type'] == Account.TYPE_LIFE_INSURANCE:
                    self.go_measure_list(account._info['measure_id_page_num'])
                    self.page.go_measure_accounts_list(account._info['measure_id'])
                    self.page.go_history(account._info)

                    if self.message.is_here():
                        self.page.submit()
                        self.page.go_history(account._info)

                    balance = self.page.get_measure_balance(account)
                    account.balance = Decimal(FrenchTransaction.clean_amount(balance))
                    account.currency = account.get_currency(balance)

        return self.accounts

    def update_linebourse_token(self):
        assert self.linebourse is not None, "linebourse browser should already exist"
        self.linebourse.session.cookies.update(self.session.cookies)
        # It is important to fetch the domain dynamically because
        # for caissedepargne the domain is 'www.caisse-epargne.offrebourse.com'
        # whereas for creditcooperatif it is 'www.offrebourse.com'
        domain = urlparse(self.url).netloc
        self.linebourse.session.headers['X-XSRF-TOKEN'] = self.session.cookies.get('XSRF-TOKEN', domain=domain)

    def add_linebourse_accounts_data(self):
        for account in self.accounts:
            self.deleteCTX()
            if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
                self.home_tache.go(tache='CPTSYNT0')
                self.page.go_history(account._info)

                if self.message.is_here():
                    self.page.submit()
                    self.page.go_history(account._info)

                # Some users may not have access to this.
                if not self.market.is_here():
                    continue
                self.page.submit()

                if 'offrebourse.com' in self.url:
                    # Some users may not have access to this.
                    if self.page.is_error():
                        continue

                    self.update_linebourse_token()
                    page = self.linebourse.go_portfolio(account.id)
                    assert self.linebourse.portfolio.is_here()
                    # We must declare "page" because this URL also matches MarketPage
                    account.valuation_diff = page.get_valuation_diff()

                    # We need to go back to the synthesis, else we can not go home later
                    self.home_tache.go(tache='CPTSYNT0')
                else:
                    raise AssertionError("new domain that hasn't been seen so far?")

    def add_card_accounts(self):
        """
        Card cases are really tricky on the new website.
        There are 2 kinds of page where we can find cards information
            - CardsPage: List some of the PSU cards
            - CardsComingPage: On the coming transaction page (for a specific checking account),
                we can find all cards related to this checking account. Information to reach this
                CC is in the home page

        We have to go through this both kind of page for those reasons:
                - If there is no coming yet, the card will not be found in the home page and we will not
                be able to reach the CardsComingPage. But we can find it on CardsPage
                - Some cards are only on the CardsComingPage and not the CardsPage
                - In CardsPage, there are cards (with "Business" in the label) without checking account on the
                website (neither history nor coming), so we skip them.
                - Some card on the CardsPage that have a checking account parent, but if we follow the link to
                reach it with CardsComingPage, we find an other card that is not in CardsPage.
        """
        if self.new_website:
            for account in self.accounts:
                # Adding card's account that we find in CardsComingPage of each Checking account
                if account._card_links:
                    self.home.go()
                    self.page.go_history(account._card_links)
                    for card in self.page.iter_cards():
                        card.parent = account
                        card._coming_info = self.page.get_card_coming_info(card.number, card.parent._card_links.copy())
                        card.ownership = account.ownership
                        self.accounts.append(card)

        self.home.go()
        self.page.go_list()
        self.page.go_cards()

        # We are on the new website. We already added some card, but we can find more of them on the CardsPage
        if self.cards.is_here():
            for card in self.page.iter_cards():
                card.parent = find_object(self.accounts, number=card._parent_id)
                assert card.parent, 'card account parent %s was not found' % card

                # If we already added this card, we don't have to add it a second time
                if find_object(self.accounts, number=card.number):
                    continue

                info = card.parent._card_links

                # If card.parent._card_links is not filled, it mean this checking account
                # has no coming transactions.
                card._coming_info = None
                card.ownership = card.parent.ownership
                if info:
                    self.page.go_list()
                    self.page.go_history(info)
                    card._coming_info = self.page.get_card_coming_info(card.number, info.copy())

                    if not card._coming_info:
                        self.logger.warning('Skip card %s (not found on checking account)', card.number)
                        continue
                self.accounts.append(card)

        # We are on the old website. We add all card that we can find on the CardsPage
        elif self.cards_old.is_here():
            for card in self.page.iter_cards():
                card.parent = find_object(self.accounts, number=card._parent_id)
                assert card.parent, 'card account parent %s was not found' % card.number
                self.accounts.append(card)

    def add_owner_accounts(self):
        owner_name = self.get_owner_name()

        if self.home.is_here():
            self.page.check_no_accounts()
            self.page.go_list()
        else:
            self.home.go()

        self.accounts = list(self.page.get_list(owner_name))

        try:
            # Get wealth accounts that are not on the summary page
            self.home_tache.go(tache='EPASYNT0')
            # If there are no wealth accounts we are redirected to the "garbage page"
            if self.home.is_here():
                for account in self.page.get_list(owner_name):
                    if account.id not in [acc.id for acc in self.accounts]:
                        self.accounts.append(account)
        except ServerError:
            self.logger.warning("Could not access wealth accounts page")

        self.add_linebourse_accounts_data()
        self.add_card_accounts()

    @need_login
    @retry(ClientError, tries=3)
    def get_accounts_list(self):
        if self.accounts is None:
            self.accounts = self.get_measure_accounts_list()
        if self.accounts is None:
            self.add_owner_accounts()

        # Some accounts have no available balance or label and cause issues
        # in the backend so we must exclude them from the accounts list:
        self.accounts = [account for account in self.accounts if account.label and account.balance != NotAvailable]
        for account in self.accounts:
            yield account

    @need_login
    def get_loans_list(self):
        if self.loans is None:
            self.loans = []

            if self.home.is_here():
                if self.page.check_no_accounts() or self.page.check_no_loans():
                    return []

            for _ in range(5):
                for _ in range(3):
                    self.home_tache.go(tache='CRESYNT0')
                    if self.home.is_here():
                        break
                if self.home.is_here():
                    if not self.page.is_access_error():
                        # The server often returns a 520 error (Undefined):
                        try:
                            self.loans = list(self.page.get_real_estate_loans())
                            self.loans.extend(self.page.get_loan_list())
                        except ServerError:
                            self.logger.warning('Access to loans failed, we try again')
                        else:
                            # We managed to reach the Loans JSON
                            break

            for _ in range(3):
                try:
                    self.home_tache.go(tache='CPTSYNT0')

                    if self.home.is_here():
                        self.page.go_list()
                except ClientError:
                    pass
                else:
                    break

        return iter(self.loans)

    # For all account, we fill up the history with transaction. For checking account, there will have
    # also deferred_card transaction too.
    # From this logic, if we send "account_card", that mean we recover all transactions from the parent
    # checking account of the account_card, then we filter later the deferred transaction.
    @need_login
    def _get_history(self, info, account_card=None):
        # Only fetch deferred debit card transactions if `account_card` is not None
        if isinstance(info['link'], list):
            info['link'] = info['link'][0]
        if not info['link'].startswith('HISTORIQUE'):
            return
        if 'measure_id' in info:
            self.go_measure_list(info['measure_id_page_num'])
            self.page.go_measure_accounts_list(info['measure_id'])
        elif self.home.is_here():
            self.page.go_list()
        else:
            self.home_tache.go(tache='CPTSYNT0')

        self.page.go_history(info)

        # ensure we are on the correct history page
        if 'netpro' in self.page.url and not self.page.is_history_of(info['id']):
            self.page.go_history_netpro(info)

        # In this case, we want the coming transaction for the new website
        # (old website return coming directly in `get_coming()` )
        if account_card and info and info['type'] == 'HISTORIQUE_CB':
            self.page.go_coming(account_card._coming_info['link'])

        info['link'] = [info['link']]

        for i in range(self.HISTORY_MAX_PAGE):

            assert self.home.is_here()

            # list of transactions on account page
            transactions_list = []
            card_and_forms = []
            for tr in self.page.get_history():
                transactions_list.append(tr)
                if tr.type == tr.TYPE_CARD_SUMMARY:
                    if account_card:
                        if self.card_matches(tr.card, account_card.number):
                            card_and_forms.append((tr.card, self.page.get_form_to_detail(tr)))
                        else:
                            self.logger.debug(
                                'will skip summary detail (%r) for different card %r',
                                tr, account_card.number
                            )
                elif tr.type == FrenchTransaction.TYPE_CARD and 'fac cb' in tr.raw.lower() and not account_card:
                    # for immediate debits made with a def card the label is way too empty for certain clients
                    # we therefore open a popup and find the rest of the label
                    # can't do that for every type of transactions because it makes a lot a additional requests
                    form = self.page.get_form_to_detail(tr)
                    transaction_popup_page = self.open(form.url, data=form)
                    tr.raw += ' ' + transaction_popup_page.page.complete_label()

            # For deferred card history only :
            #
            # Now that we find transactions that have TYPE_CARD_SUMMARY on the checking account AND the account_card number we want,
            # we browse deferred card transactions that are resume by that list of TYPE_CARD_SUMMARY transaction.

            # Checking account transaction:
            #  - 01/01 - Summary 5134XXXXXX103 - 900.00€ - TYPE_CARD_SUMMARY  <-- We have to go in the form of this tr to get
            #   cards details transactions.
            for card, form in card_and_forms:
                form.submit()
                if self.home.is_here() and self.page.is_access_error():
                    self.logger.warning('Access to card details is unavailable for this user')
                    continue
                assert self.transaction_detail.is_here()
                for tr in self.page.get_detail():
                    tr.type = Transaction.TYPE_DEFERRED_CARD
                    if account_card:
                        tr.card = card
                        tr.bdate = tr.rdate
                    transactions_list.append(tr)
                if self.new_website:
                    self.page.go_newsite_back_to_summary()
                else:
                    self.page.go_form_to_summary()

                # going back to summary goes back to first page
                for _ in range(i):
                    assert self.page.go_next()

            #  order by date the transactions without the summaries
            transactions_list = sorted_transactions(transactions_list)

            for tr in transactions_list:
                yield tr

            assert self.home.is_here()

            if not self.page.go_next():
                return

        raise AssertionError('More than {} history pages'.format(self.HISTORY_MAX_PAGE))

    @need_login
    def _get_history_invests(self, account):
        if self.home.is_here():
            self.page.go_list()
        else:
            self.home.go()

        self.page.go_history(account._info)
        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION, Account.TYPE_PERP):
            if self.page.is_account_inactive(account.id):
                self.logger.warning('Account %s %s is inactive.' % (account.label, account.id))
                return []

            # There is (currently ?) no history for MILLEVIE PREMIUM accounts
            if "MILLEVIE" in account.label:
                try:
                    self.page.go_life_insurance(account)
                except ServerError as ex:
                    if ex.response.status_code == 500 and 'MILLEVIE PREMIUM' in account.label:
                        self.logger.info("Can not reach history page for MILLEVIE PREMIUM account")
                        return []
                    raise

                label = account.label.split()[-1]
                try:
                    self.natixis_life_ins_his.go(id1=label[:3], id2=label[3:5], id3=account.id)
                except BrowserHTTPError as e:
                    if e.response.status_code == 500:
                        error = json.loads(e.response.text)
                        raise BrowserUnavailable(error["error"])
                    raise
                return sorted_transactions(self.page.get_history())

            if account.label.startswith('NUANCES ') or account.label in self.insurance_accounts:
                # Some life insurances are not on the accounts summary
                self.home_tache.go(tache='EPASYNT0')
                self.page.go_life_insurance(account)
                if 'JSESSIONID' in self.session.cookies:
                    # To access the life insurance space, we need to delete the JSESSIONID cookie to avoid an expired session
                    del self.session.cookies['JSESSIONID']

            if self.home.is_here():
                # no detail available for this account
                return []

            try:
                if not self.life_insurance.is_here() and not self.message.is_here():
                    # life insurance website is not always available
                    raise BrowserUnavailable()
                self.page.submit()
                self.life_insurance_history.go()
                # Life insurance transactions are not sorted by date in the JSON
                return sorted_transactions(self.page.iter_history())
            except (IndexError, AttributeError) as e:
                self.logger.error(e)
                return []
            except ServerError as e:
                if e.response.status_code == 500:
                    raise BrowserUnavailable()
                raise
        return self.page.iter_history()

    @need_login
    def get_history(self, account):
        self.home.go()
        self.deleteCTX()

        if account.type == account.TYPE_CARD:
            def match_cb(tr):
                return self.card_matches(tr.card, account.number)

            hist = self._get_history(account.parent._info, account)
            hist = keep_only_card_transactions(hist, match_cb)
            return hist

        if not hasattr(account, '_info'):
            raise NotImplementedError
        if (
            account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION)
            and 'measure_id' not in account._info
        ):
            return self._get_history_invests(account)
        if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
            self.page.go_history(account._info)
            if "Bourse" in self.url:
                self.page.submit()
                if 'offrebourse.com' in self.url:
                    # Some users may not have access to this.
                    if self.page.is_error():
                        return []

                    self.linebourse.session.cookies.update(self.session.cookies)
                    self.update_linebourse_token()
                    return self.linebourse.iter_history(account.id)

        hist = self._get_history(account._info, False)
        return omit_deferred_transactions(hist)

    @need_login
    def get_coming(self, account):
        if account.type == account.TYPE_CHECKING:
            return self.get_coming_checking(account)
        elif account.type == account.TYPE_CARD:
            return self.get_coming_card(account)
        return []

    def get_coming_checking(self, account):
        # The accounts list or account history page does not contain comings for checking accounts
        # We need to go to a specific levies page where we can find past and coming levies (such as recurring ones)
        trs = []
        self.home.go()
        self.page.go_cards()  # need to go to cards page to have access to the nav bar where we can choose LeviesPage from
        if not self.page.levies_page_enabled():
            return trs
        self.page.go_levies()  # need to go to a general page where we find levies for all accounts before requesting a specific account
        if not self.page.comings_enabled(account.id):
            return trs
        self.page.go_levies(account.id)
        if self.new_checkings_levies.is_here() or self.old_checkings_levies.is_here():
            today = datetime.datetime.today().date()
            # Today transactions are in this page but also in history page, we need to ignore it as a coming
            for tr in self.page.iter_coming():
                if tr.date > today:
                    trs.append(tr)
        return trs

    def get_coming_card(self, account):
        trs = []
        if not hasattr(account.parent, '_info'):
            raise NotImplementedError()
        # We are on the old website
        if hasattr(account, '_coming_eventargument'):
            if not self.cards_old.is_here():
                self.home.go()
                self.page.go_list()
                self.page.go_cards()
            self.page.go_card_coming(account._coming_eventargument)
            return sorted_transactions(self.page.iter_coming())
        # We are on the new website.
        info = account.parent._card_links
        # if info is empty, that means there are no comings yet
        if info:
            for tr in self._get_history(info.copy(), account):
                tr.type = tr.TYPE_DEFERRED_CARD
                trs.append(tr)
        return sorted_transactions(trs)

    @need_login
    def get_investment(self, account):
        self.deleteCTX()

        investable_types = (
            Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION,
            Account.TYPE_MARKET, Account.TYPE_PEA,
        )
        if (
            account.type not in investable_types
            or 'measure_id' in account._info
        ):
            raise NotImplementedError()

        if account.type == Account.TYPE_PEA and account.label == 'PEA NUMERAIRE':
            yield create_french_liquidity(account.balance)
            return

        if self.home.is_here():
            self.page.go_list()
        else:
            self.home.go()

        self.page.go_history(account._info)
        if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
            # Some users may not have access to this.
            if not self.market.is_here():
                return
            self.page.submit()

            if 'offrebourse.com' in self.url:
                # Some users may not have access to this.
                if self.page.is_error():
                    return

                self.update_linebourse_token()
                for investment in self.linebourse.iter_investments(account.id):
                    yield investment

                # We need to go back to the synthesis, else we can not go home later
                self.home_tache.go(tache='CPTSYNT0')
                return

        elif account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION):
            if self.page.is_account_inactive(account.id):
                self.logger.warning('Account %s %s is inactive.' % (account.label, account.id))
                return
            if "MILLEVIE" in account.label:
                try:
                    self.page.go_life_insurance(account)
                except ServerError as ex:
                    if ex.response.status_code == 500 and 'MILLEVIE PREMIUM' in account.label:
                        self.logger.info("Can not reach investment page for MILLEVIE PREMIUM account")
                        return
                    raise

                label = account.label.split()[-1]
                self.natixis_life_ins_inv.go(id1=label[:3], id2=label[3:5], id3=account.id)
                for tr in self.page.get_investments():
                    yield tr
                return

            try:
                # Some life insurances are not on the accounts summary
                self.home_tache.go(tache='EPASYNT0')
                self.page.go_life_insurance(account)
                if self.home.is_here():
                    # no detail is available for this account
                    return

                elif not self.market.is_here() and not self.message.is_here():
                    # life insurance website is not always available
                    raise BrowserUnavailable()

                self.page.submit()
                self.life_insurance_investments.go()
            except (IndexError, AttributeError) as e:
                self.logger.error(e)
                return

        if self.garbage.is_here():
            self.page.come_back()
            return
        for i in self.page.iter_investment():
            yield i
        if self.market.is_here():
            self.page.come_back()

    @need_login
    def iter_market_orders(self, account):
        if account.type not in (Account.TYPE_MARKET, Account.TYPE_PEA):
            return
        self.home.go()
        self.deleteCTX()
        self.page.go_history(account._info)
        if "Bourse" in self.url:
            self.page.submit()
            if 'offrebourse.com' in self.url:
                # Some users may not have access to this.
                if self.page.is_error():
                    return
                self.linebourse.session.cookies.update(self.session.cookies)
                self.update_linebourse_token()
                for order in self.linebourse.iter_market_orders(account.id):
                    yield order

    @need_login
    def get_advisor(self):
        raise NotImplementedError()

    @need_login
    def get_profile(self):
        profile = Profile()
        if len([k for k in self.session.cookies.keys() if k == 'CTX']) > 1:
            del self.session.cookies['CTX']

        ctx = decode_utf8_cookie(self.session.cookies.get('CTX', str()))
        # str() to make sure a native str is used as expected by decode_utf8_cookie
        headerdei = decode_utf8_cookie(self.session.cookies.get('headerdei', str()))
        if 'username=' in ctx:
            profile.name = re.search('username=([^&]+)', ctx).group(1)
        elif 'nomusager=' in headerdei:
            profile.name = re.search('nomusager=(?:[^&]+/ )?([^&]+)', headerdei).group(1)
        return profile

    @need_login
    def iter_recipients(self, origin_account):
        if origin_account.type in [Account.TYPE_LOAN, Account.TYPE_CARD]:
            return []

        if 'pro' in self.url:
            # If transfer is not yet allowed, the next step will send a sms to the customer to validate it
            self.home.go()
            self.page.go_pro_transfer_availability()
            if not self.page.is_transfer_allowed():
                return []

        # Transfer unavailable
        try:
            self.pre_transfer(origin_account)
        except TransferBankError:
            return []

        go_transfer_errors = (
            # redirected to home page because:
            # - need to relogin, see `self.page.need_auth()`
            # - need more security, see `self.page.transfer_unavailable()`
            # - transfer is not available for this connection, see `self.page.go_transfer_via_history()`
            # TransferPage inherit from IndexPage so self.home.is_here() is true, check page type to avoid this problem
            type(self.page) is IndexPage,
            # check if origin_account have recipients
            self.transfer.is_here() and not self.page.can_transfer(origin_account),
        )
        if any(go_transfer_errors):
            return []

        return self.page.iter_recipients(account_id=origin_account.id)

    def pre_transfer(self, account):
        if self.home.is_here():
            if 'measure_id' in account._info:
                self.go_measure_list(account._info['measure_id_page_num'])
                self.page.go_measure_accounts_list(account._info['measure_id'])
            else:
                self.page.go_list()
        else:
            self.home.go()
        self.page.go_transfer(account)

    @need_login
    def init_transfer(self, account, recipient, transfer):
        self.is_send_sms = False
        self.is_app_validation = False
        self.pre_transfer(account)

        if self.pro_transfer.is_here():
            # OTP validation does not work for pro users, and all transfers
            # requires an otp validation.
            raise NotImplementedError()

        # Warning: this may send a sms or an app validation
        self.page.init_transfer(account, recipient, transfer)

        if self.validation_option.is_here():
            self.get_auth_mechanisms_validation_info()

            if self.otp_validation['type'] == 'SMS':
                self.is_send_sms = True
                raise TransferStep(
                    transfer,
                    Value(
                        'otp_sms',
                        label='Veuillez renseigner le mot de passe unique qui vous a été envoyé par SMS dans le champ réponse.'
                    )
                )
            elif self.otp_validation['type'] == 'CLOUDCARD':
                self.is_app_validation = True
                raise AppValidation(
                    resource=transfer,
                    message="Veuillez valider le transfert sur votre application mobile.",
                )

        if 'netpro' in self.url:
            return self.page.create_transfer(account, recipient, transfer)

        self.page.continue_transfer(account.label, recipient.label, transfer.label)
        return self.page.update_transfer(transfer, account, recipient)

    @need_login
    def otp_validation_continue_transfer(self, transfer, **params):
        assert (
            'resume' in params
            or 'otp_sms' in params
        ), 'otp_sms or resume is missing'

        if 'resume' in params:
            self.is_app_validation = False

            self.do_authentication_validation(
                authentication_method='CLOUDCARD',
                feature='transfer',
            )
        elif 'otp_sms' in params:
            self.is_send_sms = False

            self.do_authentication_validation(
                authentication_method='SMS',
                feature='transfer',
                otp_sms=params['otp_sms']
            )

        if self.transfer.is_here():
            self.page.continue_transfer(transfer.account_label, transfer.recipient_label, transfer.label)
            return self.page.update_transfer(transfer)
        raise AssertionError('Blank page instead of the TransferPage')

    @need_login
    def execute_transfer(self, transfer):
        self.page.confirm()
        return self.page.populate_reference(transfer)

    def get_recipient_obj(self, recipient):
        r = Recipient()
        r.iban = recipient.iban
        r.id = recipient.iban
        r.label = recipient.label
        r.category = u'Externe'
        r.enabled_at = datetime.datetime.now().replace(microsecond=0)
        r.currency = u'EUR'
        r.bank_name = NotAvailable
        return r

    def post_sms_password(self, otp, otp_field_xpath):
        data = {}
        for k, v in self.recipient_form.items():
            if k != 'url':
                data[k] = v
        data[otp_field_xpath] = otp
        self.location(self.recipient_form['url'], data=data)
        self.recipient_form = None

    def facto_post_recip(self, recipient):
        self.page.post_recipient(recipient)
        self.page.confirm_recipient()
        return self.get_recipient_obj(recipient)

    def end_sms_recipient(self, recipient, **params):
        self.post_sms_password(params['sms_password'], 'uiAuthCallback__1_')
        self.page.post_form()
        self.page.go_on()
        self.facto_post_recip(recipient)

    def end_pro_recipient(self, recipient, **params):
        self.post_sms_password(params['pro_password'], 'MM$ANR_WS_AUTHENT$ANR_WS_AUTHENT_SAISIE$txtReponse')
        return self.facto_post_recip(recipient)

    @retry(CanceledAuth)
    @need_login
    def new_recipient(self, recipient, **params):
        if 'sms_password' in params:
            return self.end_sms_recipient(recipient, **params)

        if 'otp_sms' in params or 'resume' in params:
            if 'otp_sms' in params:
                self.do_authentication_validation(
                    authentication_method='SMS',
                    otp_sms=params['otp_sms'],
                    feature='recipient'
                )
            else:
                self.do_authentication_validation(
                    authentication_method='CLOUDCARD',
                    feature='recipient'
                )

            if self.authent.is_here():
                self.page.go_on()
                return self.facto_post_recip(recipient)

        if 'pro_password' in params:
            return self.end_pro_recipient(recipient, **params)

        first_transfer_account = next(
            acc
            for acc in self.get_accounts_list()
            if acc.type in (Account.TYPE_CHECKING, Account.TYPE_SAVINGS)
        )
        self.pre_transfer(first_transfer_account)
        # This send sms to user.
        self.page.go_add_recipient()

        if self.transfer.is_here():
            self.page.handle_error()
            raise AssertionError('We should not be on this page')

        if self.home.is_here():
            # If we land here it might be because the user has no 2fa method
            # enabled, and therefore cannot add a recipient.
            unavailable_2fa = self.page.get_unavailable_2fa_message()
            if unavailable_2fa:
                raise AddRecipientBankError(message=unavailable_2fa)
            raise AssertionError('Should not be on home page after sending sms when adding new recipient.')

        if self.validation_option.is_here():
            self.get_auth_mechanisms_validation_info()

            recipient_obj = self.get_recipient_obj(recipient)
            if self.otp_validation['type'] == 'SMS':
                self.is_send_sms = True
                raise AddRecipientStep(
                    recipient_obj,
                    Value(
                        'otp_sms',
                        label='Veuillez renseigner le mot de passe unique qui vous a été envoyé par SMS dans le champ réponse.'
                    )
                )
            elif self.otp_validation['type'] == 'CLOUDCARD':
                self.is_app_validation = True
                raise AppValidation(
                    resource=recipient_obj,
                    message="Veuillez valider l'ajout de bénéficiaire sur votre application mobile."
                )

        # pro add recipient.
        elif self.page.need_auth():
            self.page.set_browser_form()
            raise AddRecipientStep(
                self.get_recipient_obj(recipient),
                Value('pro_password', label=self.page.get_prompt_text())
            )
        else:
            self.page.check_canceled_auth()
            self.page.set_browser_form()
            raise AddRecipientStep(
                self.get_recipient_obj(recipient),
                Value('sms_password', label=self.page.get_prompt_text())
            )

    def go_documents_without_sub(self):
        self.home_tache.go(tache='CPTSYNT0')
        assert self.subscription.is_here(), "Couldn't go to documents page"

    @need_login
    def iter_subscription(self):
        self.home.go()
        # CapDocument is not implemented for professional accounts yet
        if any(x in self.url for x in ["netpp", "netpro"]):
            raise NotImplementedError()
        self.home_tache.go(tache='CPTSYNT1')
        if self.unavailable_page.is_here():
            # some users don't have checking account
            self.home_tache.go(tache='EPASYNT0')
        if self.garbage.is_here():  # User has no subscription, checking if they have documents, if so creating fake subscription
            self.has_subscription = False
            self.home_tache.go(tache='CPTSYNT0')
            if not self.subscription.is_here():  # Looks like there is nothing to return
                return []
            self.logger.warning("Couldn't find subscription, creating a fake one to return documents available")

            profile = self.get_profile()

            sub = Subscription()
            sub.label = sub.subscriber = profile.name
            sub.id = sha256(profile.name.lower().encode('utf-8')).hexdigest()

            return [sub]

        # if we are not on checkings page, we don't have documents
        if not self.checking.is_here():
            return []

        self.page.go_subscription()
        if not self.subscription.is_here():
            # if user is not allowed to have subscription we are redirected to IndexPage
            assert self.home.is_here() and self.page.is_subscription_unauthorized()
            return []

        if self.page.has_subscriptions():
            return self.page.iter_subscription()
        return []

    @need_login
    def iter_documents(self, subscription):
        self.home.go()
        if not self.has_subscription:
            self.go_documents_without_sub()
            for doc in self.page.iter_documents(sub_id=subscription.id, has_subscription=self.has_subscription):
                yield doc
        else:
            today = date.today()

            self.home_tache.go(tache='CPTSYNT1')
            if self.unavailable_page.is_here():
                # some users don't have checking account
                self.home_tache.go(tache='EPASYNT0')
            self.page.go_subscription()
            # setting to have 3 years of history
            for year in range(today.year - 2, today.year + 1):
                self.page.change_year(year)

                assert self.subscription.is_here()

                for doc in self.page.iter_documents(sub_id=subscription.id, has_subscription=self.has_subscription):
                    yield doc

    @need_login
    def download_document(self, document):
        self.home.go()
        if not self.has_subscription:
            self.go_documents_without_sub()
            return self.page.download_document(document).content
        self.home_tache.go(tache='CPTSYNT1')
        if self.unavailable_page.is_here():
            # some users don't have checking account
            self.home_tache.go(tache='EPASYNT0')
        self.page.go_subscription()
        assert self.subscription.is_here()

        return self.page.download_document(document).content

    def card_matches(self, a, b):
        # For the same card, depending where we scrape it, we have
        # more or less visible number. `X` are visible number, `*` hidden one's.
        # tr.card: XXXX******XXXXXX, account.number: XXXXXX******XXXX
        return (a[:4], a[-4:]) == (b[:4], b[-4:])

    @need_login
    def iter_transfers(self, account):
        self.home.go()
        self.page.go_checkings()
        self.page.go_transfer_list()

        for transfer in self.page.iter_transfers():
            self.page.open_transfer(transfer._formarg)
            self.page.fill_transfer(obj=transfer)
            yield transfer

    @need_login
    def iter_emitters(self):
        self.home.go()
        if self.page.go_emitters() is False:
            return []
        return self.page.iter_emitters()
