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

from __future__ import unicode_literals

import re
import datetime
import json

from decimal import Decimal
from dateutil import parser

from weboob.browser import LoginBrowser, need_login, StatesMixin
from weboob.browser.switch import SiteSwitch
from weboob.browser.url import URL
from weboob.capabilities.bank import Account, AddRecipientStep, Recipient, TransferBankError, Transaction, TransferStep
from weboob.capabilities.base import NotAvailable
from weboob.capabilities.profile import Profile
from weboob.browser.exceptions import BrowserHTTPNotFound, ClientError
from weboob.exceptions import (
    BrowserIncorrectPassword, BrowserUnavailable, BrowserHTTPError, BrowserPasswordExpired, ActionNeeded
)
from weboob.tools.capabilities.bank.transactions import sorted_transactions, FrenchTransaction
from weboob.tools.capabilities.bank.investments import create_french_liquidity
from weboob.tools.compat import urljoin, urlparse
from weboob.tools.value import Value
from weboob.tools.decorators import retry

from .pages import (
    IndexPage, ErrorPage, MarketPage, LifeInsurance, GarbagePage,
    MessagePage, LoginPage,
    TransferPage, ProTransferPage, TransferConfirmPage, TransferSummaryPage, ProTransferConfirmPage,
    ProTransferSummaryPage, ProAddRecipientOtpPage, ProAddRecipientPage,
    SmsPage, SmsPageOption, SmsRequest, AuthentPage, RecipientPage, CanceledAuth, CaissedepargneKeyboard,
    TransactionsDetailsPage, LoadingPage, ConsLoanPage, MeasurePage, NatixisLIHis, NatixisLIInv, NatixisRedirectPage,
    SubscriptionPage, CreditCooperatifMarketPage, UnavailablePage,
)

from .linebourse_browser import LinebourseAPIBrowser


__all__ = ['CaisseEpargne']


class CaisseEpargne(LoginBrowser, StatesMixin):
    BASEURL = "https://www.caisse-epargne.fr"
    STATE_DURATION = 5
    HISTORY_MAX_PAGE = 200

    LINEBOURSE_BROWSER = LinebourseAPIBrowser

    login = URL('/authentification/manage\?step=identification&identifiant=(?P<login>.*)',
                'https://.*/login.aspx', LoginPage)
    account_login = URL('/authentification/manage\?step=account&identifiant=(?P<login>.*)&account=(?P<accountType>.*)', LoginPage)
    loading = URL('https://.*/CreditConso/ReroutageCreditConso.aspx', LoadingPage)
    cons_loan = URL('https://www.credit-conso-cr.caisse-epargne.fr/websavcr-web/rest/contrat/getContrat\?datePourIe(?P<datepourie>)', ConsLoanPage)
    transaction_detail = URL('https://.*/Portail.aspx.*', TransactionsDetailsPage)
    recipient = URL('https://.*/Portail.aspx.*', RecipientPage)
    transfer = URL('https://.*/Portail.aspx.*', TransferPage)
    transfer_summary = URL('https://.*/Portail.aspx.*', TransferSummaryPage)
    transfer_confirm = URL('https://.*/Portail.aspx.*', TransferConfirmPage)
    pro_transfer = URL('https://.*/Portail.aspx.*', ProTransferPage)
    pro_transfer_confirm = URL('https://.*/Portail.aspx.*', ProTransferConfirmPage)
    pro_transfer_summary = URL('https://.*/Portail.aspx.*', ProTransferSummaryPage)
    pro_add_recipient_otp = URL('https://.*/Portail.aspx.*', ProAddRecipientOtpPage)
    pro_add_recipient = URL('https://.*/Portail.aspx.*', ProAddRecipientPage)
    measure_page = URL('https://.*/Portail.aspx.*', MeasurePage)
    authent = URL('https://.*/Portail.aspx.*', AuthentPage)
    subscription = URL('https://.*/Portail.aspx\?tache=(?P<tache>).*', SubscriptionPage)
    home = URL('https://.*/Portail.aspx.*', IndexPage)
    home_tache = URL('https://.*/Portail.aspx\?tache=(?P<tache>).*', IndexPage)
    error = URL('https://.*/login.aspx',
                'https://.*/Pages/logout.aspx.*',
                'https://.*/particuliers/Page_erreur_technique.aspx.*', ErrorPage)
    market = URL('https://.*/Pages/Bourse.*',
                 'https://www.caisse-epargne.offrebourse.com/ReroutageSJR',
                 r'https://www.caisse-epargne.offrebourse.com/fr/6CE.*', MarketPage)
    unavailable_page = URL('https://www.caisse-epargne.fr/.*/au-quotidien', UnavailablePage)

    creditcooperatif_market = URL('https://www.offrebourse.com/.*', CreditCooperatifMarketPage)  # just to catch the landing page of the Credit Cooperatif's Linebourse
    natixis_redirect = URL(r'/NaAssuranceRedirect/NaAssuranceRedirect.aspx',
                           r'https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/views/common/routage-itce.xhtml\?windowId=automatedEntryPoint',
                           NatixisRedirectPage)
    life_insurance = URL('https://.*/Assurance/Pages/Assurance.aspx',
                         'https://www.extranet2.caisse-epargne.fr.*', LifeInsurance)
    natixis_life_ins_his = URL('https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/rest/v2/contratVie/load-operation/(?P<id1>\w+)/(?P<id2>\w+)/(?P<id3>)', NatixisLIHis)
    natixis_life_ins_inv = URL('https://www.espace-assurances.caisse-epargne.fr/espaceinternet-ce/rest/v2/contratVie/load/(?P<id1>\w+)/(?P<id2>\w+)/(?P<id3>)', NatixisLIInv)
    message = URL('https://www.caisse-epargne.offrebourse.com/DetailMessage\?refresh=O', MessagePage)
    garbage = URL('https://www.caisse-epargne.offrebourse.com/Portefeuille',
                  'https://www.caisse-epargne.fr/particuliers/.*/emprunter.aspx',
                  'https://.*/particuliers/emprunter.*',
                  'https://.*/particuliers/epargner.*', GarbagePage)
    sms = URL('https://www.icgauth.caisse-epargne.fr/dacswebssoissuer/AuthnRequestServlet', SmsPage)
    sms_option = URL('https://www.icgauth.caisse-epargne.fr/dacstemplate-SOL/index.html\?transactionID=.*', SmsPageOption)
    request_sms = URL('https://www.icgauth.caisse-epargne.fr/dacsrest/api/v1u0/transaction/(?P<param>)', SmsRequest)
    __states__ = ('BASEURL', 'multi_type', 'typeAccount', 'is_cenet_website', 'recipient_form', 'is_send_sms')

    # Accounts managed in life insurance space (not in linebourse)

    insurance_accounts = ('AIKIDO',
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
                          'YOGA', )

    def __init__(self, nuser, *args, **kwargs):
        self.BASEURL = kwargs.pop('domain', self.BASEURL)
        if not self.BASEURL.startswith('https://'):
            self.BASEURL = 'https://%s' % self.BASEURL

        self.is_cenet_website = False
        self.multi_type = False
        self.accounts = None
        self.loans = None
        self.typeAccount = None
        self.inexttype = 0  # keep track of index in the connection type's list
        self.nuser = nuser
        self.recipient_form = None
        self.is_send_sms = None
        self.weboob = kwargs['weboob']
        self.market_url = kwargs.pop(
            'market_url',
            'https://www.caisse-epargne.offrebourse.com',
        )

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

    def deleteCTX(self):
        # For connection to offrebourse and natixis, we need to delete duplicate of CTX cookie
        if len([k for k in self.session.cookies.keys() if k == 'CTX']) > 1:
            del self.session.cookies['CTX']

    def load_state(self, state):
        if state.get('expire') and parser.parse(state['expire']) < datetime.datetime.now():
            return self.logger.info('State expired, not reloading it from storage')

        # Reload session only for add recipient step
        transfer_states = ('recipient_form', 'is_send_sms')

        for transfer_state in transfer_states:
            if transfer_state in state and state[transfer_state] is not None:
                super(CaisseEpargne, self).load_state(state)
                self.logged = True
                break

    # need to post to valid otp when adding recipient.
    def locate_browser(self, state):
        if 'is_send_sms' in state and state['is_send_sms'] is not None:
            super(CaisseEpargne, self).locate_browser(state)

    def do_login(self):
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

        # Retrieve the list of types: can contain a single type or more
        # - when there is a single type: all the information are available
        # - when there are several types: an additional request is needed
        try:
            connection = self.login.go(login=self.username)
        # The website crash sometime when the module is not on caissedepargne (on linebourse, for exemple).
        # The module think is not connected anymore, so we go to the home logged page. If there are no error
        # that mean we are already logged and now, on the good website

        except ValueError:
            self.home.go()
            if self.home.is_here():
                return
            # If that not the case, that's an other error that we have to correct
            raise

        data = connection.get_response()

        if data is None:
            raise BrowserIncorrectPassword()

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

                self.typeAccount = accounts_types[self.inexttype]
            else:
                assert False, 'should have logged in with at least one connection type'
            self.inexttype += 1

            data = self.account_login.go(login=self.username, accountType=self.typeAccount).get_response()

        assert data is not None

        if data.get('authMode', '') == 'redirect':  # the connection type EU could also be used as a criteria
            raise SiteSwitch('cenet')

        typeAccount = data['account'][0]

        if self.multi_type:
            assert typeAccount == self.typeAccount

        id_token_clavier = data['keyboard']['Id']
        vk = CaissedepargneKeyboard(data['keyboard']['ImageClavier'], data['keyboard']['Num']['string'])
        newCodeConf = vk.get_string_code(self.password)

        playload = {
            'idTokenClavier': id_token_clavier,
            'newCodeConf': newCodeConf,
            'auth_mode': 'ajax',
            'nuusager': self.nuser.encode('utf-8'),
            'codconf': '',  # must be present though empty
            'typeAccount': typeAccount,
            'step': 'authentification',
            'ctx': 'typsrv={}'.format(typeAccount),
            'clavierSecurise': '1',
            'nuabbd': self.username
        }

        try:
            res = self.location(data['url'], params=playload)
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
                raise BrowserIncorrectPassword(response['error'])

            # we tested all, next iteration will throw the assertion
            if self.inexttype == len(accounts_types) and 'Temporairement votre abonnement est bloqué' in response['error']:
                raise ActionNeeded(response['error'])

            if self.multi_type:
                # try to log in with the next connection type's value
                self.do_login()
                return
            raise BrowserIncorrectPassword(response['error'])

        self.BASEURL = urljoin(data['url'], '/')

        try:
            self.home.go()
        except BrowserHTTPNotFound:
            raise BrowserIncorrectPassword()

    def loans_conso(self):
        days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
        month = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
        now = datetime.datetime.today()
        d = '%s %s %s %s %s:%s:%s GMT+0100 (heure normale d’Europe centrale)' % (days[now.weekday()], now.day, month[now.month - 1], now.year, now.hour, format(now.minute, "02"), now.second)
        if self.home.is_here():
            msg = self.page.loan_unavailable_msg()
            if msg:
                self.logger.warning('%s' % msg)
                return None
        self.cons_loan.go(datepourie=d)
        return self.page.get_conso()

    # On home page there is a list of "measure" links, each one leading to one person accounts list.
    # Iter over each 'measure' and navigate to it to get all accounts
    @need_login
    def get_measure_accounts_list(self):
        self.home.go()

        # Make sure we are on list of measures page
        if self.measure_page.is_here():
            self.page.check_no_accounts()
            measure_ids = self.page.get_measure_ids()
            self.accounts = []
            for measure_id in measure_ids:
                self.page.go_measure_accounts_list(measure_id)
                if self.page.check_measure_accounts():
                    for account in list(self.page.get_list()):
                        account._info['measure_id'] = measure_id
                        self.accounts.append(account)
                self.page.go_measure_list()

            for account in self.accounts:
                if 'acc_type' in account._info and account._info['acc_type'] == Account.TYPE_LIFE_INSURANCE:
                    self.page.go_measure_list()
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

    @need_login
    @retry(ClientError, tries=3)
    def get_accounts_list(self):
        if self.accounts is None:
            self.accounts = self.get_measure_accounts_list()
        if self.accounts is None:
            if self.home.is_here():
                self.page.check_no_accounts()
                self.page.go_list()
            else:
                self.home.go()

            self.accounts = list(self.page.get_list())
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
                    else:
                        assert False, "new domain that hasn't been seen so far ?"

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

            for _ in range(3):
                self.home_tache.go(tache='CRESYNT0')
                if self.home.is_here():
                    break

            if self.home.is_here():
                if not self.page.is_access_error():
                    self.loans = list(self.page.get_real_estate_loans())
                    self.loans.extend(self.page.get_loan_list())

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

    @need_login
    def _get_history(self, info):
        if isinstance(info['link'], list):
            info['link'] = info['link'][0]
        if not info['link'].startswith('HISTORIQUE'):
            return
        if 'measure_id' in info:
            self.page.go_measure_list()
            self.page.go_measure_accounts_list(info['measure_id'])
        elif self.home.is_here():
            self.page.go_list()
        else:
            self.home_tache.go(tache='CPTSYNT0')

        self.page.go_history(info)

        # ensure we are on the correct history page
        if 'netpro' in self.page.url and not self.page.is_history_of(info['id']):
            self.page.go_history_netpro(info)

        info['link'] = [info['link']]

        for i in range(self.HISTORY_MAX_PAGE):

            assert self.home.is_here()

            # list of transactions on account page
            transactions_list = []
            list_form = []
            for tr in self.page.get_history():
                transactions_list.append(tr)
                if tr.type == tr.TYPE_CARD_SUMMARY:
                    list_form.append(self.page.get_form_to_detail(tr))

            # add detail card to list of transactions
            for form in list_form:
                form.submit()
                if self.home.is_here() and self.page.is_access_error():
                    self.logger.warning('Access to card details is unavailable for this user')
                    continue
                assert self.transaction_detail.is_here()
                for tr in self.page.get_detail():
                    tr.type = Transaction.TYPE_DEFERRED_CARD
                    transactions_list.append(tr)
                if self.new_website:
                    self.page.go_newsite_back_to_summary()
                else:
                    self.page.go_form_to_summary()

                # going back to summary goes back to first page
                for j in range(i):
                    assert self.page.go_next()

            #  order by date the transactions without the summaries
            transactions_list = sorted_transactions(transactions_list)

            for tr in transactions_list:
                yield tr

            assert self.home.is_here()

            if not self.page.go_next():
                return

        assert False, 'More than {} history pages'.format(self.HISTORY_MAX_PAGE)

    @need_login
    def _get_history_invests(self, account):
        if self.home.is_here():
            self.page.go_list()
        else:
            self.home.go()

        self.page.go_history(account._info)
        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_PERP):
            if self.page.is_account_inactive(account.id):
                self.logger.warning('Account %s %s is inactive.' % (account.label, account.id))
                return []
            if "MILLEVIE" in account.label:
                self.page.go_life_insurance(account)
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
                self.page.go_life_insurance(account)
                if 'JSESSIONID' in self.session.cookies:
                    # To access the life insurance space, we need to delete the JSESSIONID cookie to avoid an expired session
                    del self.session.cookies['JSESSIONID']

            try:
                if not self.life_insurance.is_here() and not self.message.is_here():
                    # life insurance website is not always available
                    raise BrowserUnavailable()
                self.page.submit()
                self.location('https://www.extranet2.caisse-epargne.fr%s' % self.page.get_cons_histo())

            except (IndexError, AttributeError) as e:
                self.logger.error(e)
                return []
        return self.page.iter_history()

    @need_login
    def get_history(self, account):
        self.home.go()
        self.deleteCTX()
        if not hasattr(account, '_info'):
            raise NotImplementedError
        if account.type is Account.TYPE_LIFE_INSURANCE and 'measure_id' not in account._info:
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
        return self._get_history(account._info)

    @need_login
    def get_coming(self, account):
        trs = []

        if not hasattr(account, '_info'):
            raise NotImplementedError()
        for info in account._card_links:
            for tr in self._get_history(info.copy()):
                tr.type = tr.TYPE_DEFERRED_CARD
                tr.nopurge = True
                trs.append(tr)

        return sorted_transactions(trs)

    @need_login
    def get_investment(self, account):
        self.deleteCTX()
        if account.type not in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION, Account.TYPE_MARKET, Account.TYPE_PEA) or 'measure_id' in account._info:
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
                return

        elif account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION):
            if self.page.is_account_inactive(account.id):
                self.logger.warning('Account %s %s is inactive.' % (account.label, account.id))
                return
            if "MILLEVIE" in account.label:
                self.page.go_life_insurance(account)
                label = account.label.split()[-1]
                self.natixis_life_ins_inv.go(id1=label[:3], id2=label[3:5], id3=account.id)
                for tr in self.page.get_investments():
                    yield tr
                return

            try:
                self.page.go_life_insurance(account)

                if not self.market.is_here() and not self.message.is_here():
                    # life insurance website is not always available
                    raise BrowserUnavailable()

                self.page.submit()
                self.location('https://www.extranet2.caisse-epargne.fr%s' % self.page.get_cons_repart())
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
    def get_advisor(self):
        raise NotImplementedError()

    @need_login
    def get_profile(self):
        from weboob.tools.misc import to_unicode
        profile = Profile()
        if len([k for k in self.session.cookies.keys() if k == 'CTX']) > 1:
            del self.session.cookies['CTX']
        elif 'username=' in self.session.cookies.get('CTX', ''):
            profile.name = to_unicode(re.search('username=([^&]+)', self.session.cookies['CTX']).group(1))
        elif 'nomusager=' in self.session.cookies.get('headerdei'):
            profile.name = to_unicode(re.search('nomusager=(?:[^&]+/ )?([^&]+)', self.session.cookies['headerdei']).group(1))
        return profile

    @need_login
    def iter_recipients(self, origin_account):
        if origin_account.type == Account.TYPE_LOAN:
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
                self.page.go_measure_list()
                self.page.go_measure_accounts_list(account._info['measure_id'])
            else:
                self.page.go_list()
        else:
            self.home.go()
        self.page.go_transfer(account)

    @need_login
    def init_transfer(self, account, recipient, transfer):
        self.is_send_sms = False
        self.pre_transfer(account)
        self.page.init_transfer(account, recipient, transfer)

        if self.sms_option.is_here():
            self.is_send_sms = True
            raise TransferStep(
                transfer,
                Value(
                    'otp_sms',
                    label='Veuillez renseigner le mot de passe unique qui vous a été envoyé par SMS dans le champ réponse.'
                )
            )

        if 'netpro' in self.url:
            return self.page.create_transfer(account, recipient, transfer)

        self.page.continue_transfer(account.label, recipient.label, transfer.label)
        return self.page.update_transfer(transfer, account, recipient)

    @need_login
    def otp_sms_continue_transfer(self, transfer, **params):
        self.is_send_sms = False
        assert 'otp_sms' in params, 'OTP SMS is missing'

        self.otp_sms_validation(params['otp_sms'])
        if self.transfer.is_here():
            self.page.continue_transfer(transfer.account_label, transfer.recipient_label, transfer.label)
            return self.page.update_transfer(transfer)

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

    def otp_sms_validation(self, otp_sms):
        tr_id = re.search(r'transactionID=(.*)', self.page.url)
        if tr_id:
            transaction_id = tr_id.group(1)
        else:
            assert False, 'Transfer transaction id was not found in url'

        self.request_sms.go(param=transaction_id)

        key = self.page.validate_key()
        data = {
            'validate': {
                key: [{
                    'id': self.page.validation_id(key),
                    'otp_sms': otp_sms,
                    'type': 'SMS'
                }]
            }
        }
        headers = {'Content-Type': 'application/json'}
        self.location(self.url + '/step', json=data, headers=headers)

        saml = self.page.get_saml()
        action = self.page.get_action()
        self.location(action, data={'SAMLResponse': saml})

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

        if 'otp_sms' in params:
            self.otp_sms_validation(params['otp_sms'])

            if self.authent.is_here():
                self.page.go_on()
                return self.facto_post_recip(recipient)

        if 'pro_password' in params:
            return self.end_pro_recipient(recipient, **params)

        self.pre_transfer(next(acc for acc in self.get_accounts_list() if acc.type in (Account.TYPE_CHECKING, Account.TYPE_SAVINGS)))
        # This send sms to user.
        self.page.go_add_recipient()

        if self.sms_option.is_here():
            self.is_send_sms = True
            raise AddRecipientStep(
                self.get_recipient_obj(recipient),
                Value(
                    'otp_sms',
                    label='Veuillez renseigner le mot de passe unique qui vous a été envoyé par SMS dans le champ réponse.'
                )
            )

        # pro add recipient.
        elif self.page.need_auth():
            self.page.set_browser_form()
            raise AddRecipientStep(self.get_recipient_obj(recipient), Value('pro_password', label=self.page.get_prompt_text()))

        else:
            self.page.check_canceled_auth()
            self.page.set_browser_form()
            raise AddRecipientStep(self.get_recipient_obj(recipient), Value('sms_password', label=self.page.get_prompt_text()))

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
        self.page.go_subscription()
        assert self.subscription.is_here()

        if self.page.has_subscriptions():
            return self.page.iter_subscription()
        return []

    @need_login
    def iter_documents(self, subscription):
        self.home.go()
        self.home_tache.go(tache='CPTSYNT1')
        if self.unavailable_page.is_here():
            # some users don't have checking account
            self.home_tache.go(tache='EPASYNT0')
        self.page.go_subscription()
        assert self.subscription.is_here()

        sub_id = subscription.id
        self.page.go_document_list(sub_id=sub_id)

        for doc in self.page.iter_documents(sub_id=sub_id):
            yield doc

    @need_login
    def download_document(self, document):
        self.home.go()
        self.home_tache.go(tache='CPTSYNT1')
        if self.unavailable_page.is_here():
            # some users don't have checking account
            self.home_tache.go(tache='EPASYNT0')
        self.page.go_subscription()
        assert self.subscription.is_here()

        sub_id = document.id.split('_')[0]
        self.page.go_document_list(sub_id=sub_id)

        return self.page.download_document(document).content
