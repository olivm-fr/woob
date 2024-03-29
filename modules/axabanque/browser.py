# -*- coding: utf-8 -*-

# flake8: compatible

# Copyright(C) 2016      Edouard Lambert
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

import base64
import random
import re
from datetime import date, datetime, timedelta
from hashlib import sha256

from dateutil.relativedelta import relativedelta

from weboob.browser import LoginBrowser, URL, need_login, StatesMixin, OAuth2PKCEMixin, PagesBrowser
from weboob.browser.exceptions import ClientError, HTTPNotFound, BrowserUnavailable
from weboob.capabilities.base import NotAvailable
from weboob.capabilities.bill import Subscription
from weboob.capabilities.bank import (
    Account, Transaction, AddRecipientStep, Recipient, AccountOwnership,
)
from weboob.capabilities.profile import Person
from weboob.exceptions import BrowserIncorrectPassword, ActionNeeded
from weboob.tools.value import Value
from weboob.tools.capabilities.bank.transactions import sorted_transactions
from weboob.tools.capabilities.bank.investments import create_french_liquidity

from .pages.login import (
    KeyboardPage, LoginPage, ChangepasswordPage, PredisconnectedPage, DeniedPage,
    AccountSpaceLogin, ErrorPage, AuthorizePage,
)
from .pages.bank import (
    AccountsPage as BankAccountsPage, CBTransactionsPage, TransactionsPage,
    UnavailablePage, IbanPage, LifeInsuranceIframe, BoursePage, BankProfilePage, BalancesPage,
)
from .pages.wealth import (
    AccountsPage as WealthAccountsPage, AccountDetailsPage, InvestmentPage,
    InvestmentMonAxaPage, HistoryPage, HistoryInvestmentsPage, ProfilePage,
    PerformanceMonAxaPage,
)
from .pages.transfer import (
    RecipientsPage, AddRecipientPage, ValidateTransferPage, RegisterTransferPage,
    ConfirmTransferPage, RecipientConfirmationPage, ScheduledTransfersPage,
    ScheduledTransferDetailsPage,
)
from .pages.document import DocumentsPage, DownloadPage


class AXABrowser(LoginBrowser):
    # Login
    keyboard = URL(r'https://connect.axa.fr/keyboard/password', KeyboardPage)
    login = URL(r'https://connect.axa.fr/api/identity/auth', LoginPage)
    password = URL(r'https://connect.axa.fr/#/changebankpassword', ChangepasswordPage)
    predisconnected = URL(
        r'https://www.axa.fr/axa-predisconnect.html',
        r'https://www.axa.fr/axa-postmaw-predisconnect.html',
        PredisconnectedPage
    )
    authorize = URL(r'https://connect.axa.fr/connect/authorize', AuthorizePage)
    denied = URL(r'https://connect.axa.fr/Account/AccessDenied', DeniedPage)
    account_space_login = URL(r'https://connect.axa.fr/api/accountspace', AccountSpaceLogin)
    errors = URL(
        r'https://espaceclient.axa.fr/content/ecc-public/accueil-axa-connect/_jcr_content/par/text.html',
        r'https://espaceclient.axa.fr/content/ecc-public/errors/500.html',
        ErrorPage
    )

    def do_login(self):
        # Due to the website change, login changed too.
        # This is for avoiding to log-in with the wrong login
        if self.username.isdigit() and len(self.username) > 7:
            raise ActionNeeded()

        if self.password.isdigit():
            self.account_space_login.go()
            if self.page.get_error_link():
                # Go on information page to get possible error message
                self.location(self.page.get_error_link())

            vk_passwd = self.keyboard.go().get_password(self.password)

            login_data = {
                'email': self.username,
                'password': vk_passwd,
                'rememberIdenfiant': False,
            }

            self.location('https://connect.axa.fr')
            self.login.go(data=login_data, headers={'X-XSRF-TOKEN': self.session.cookies['XSRF-TOKEN']})

        if not self.password.isdigit() or self.page.check_error():
            raise BrowserIncorrectPassword()

        url = self.page.get_url()
        if 'bank-otp' in url:
            # The SCA is Cross-Browser so the user can do the SMS validation on the website
            # and then try to synchronize the connection again.
            raise ActionNeeded('Vous devez réaliser la double authentification sur le portail internet')

        # home page to finish login
        self.location('https://espaceclient.axa.fr/')


class AXABanque(OAuth2PKCEMixin, PagesBrowser):
    BASEURL = 'https://banque.axa.fr'

    BALANCE_TYPE = 'WEB'  # or OTHR, AODA, OTHR at end-of-last-month, CLBD, XPCD

    AUTHORIZATION_URI  = 'https://api-banque.axa.fr/oauth/authorize'
    AUTHORIZATION2_URI = 'https://api-banque.axa.fr/oauth/authorization-code'
    ACCESS_TOKEN_URI   = 'https://api-banque.axa.fr/oauth/token'

    redirect_uri   = 'https://banque.axa.fr/auth/checkuser'
    check_user_uri = 'https://api-banque.axa.fr/securityapi/checkuser'
    js_hashes_uri  = 'https://banque.axa.fr/novatio-core/hash-ref.json?nocache=true'

    app          = URL(r'https://banque.axa.fr/novatio-modules/web-axb/(?P<hash>)/app.js', None)
    login        = URL(r'https://banque.axa.fr/auth/login', LoginPage)
    balances     = URL(r'https://api-banque.axa.fr/distri-account-api/api/v1/customers/me/accounts/(?P<id_account>\w+)/balances', BalancesPage)
    transactions = URL(r'https://api-banque.axa.fr/distri-account-api/api/v1/persons/me/accounts/(?P<id_account>\w+)/transactions', TransactionsPage)
    accounts     = URL(r'https://api-banque.axa.fr/distri-account-api/api/v1/customers/me/accounts', BankAccountsPage)  # must be after the others

    def __init__(self, login, password, *args, **kwargs):
        super(AXABanque,self).__init__(*args, **kwargs)

        self.username = login
        self.password = password

        self._fetch_auth_parameters()

    def _fetch_auth_parameters(self):
        hashes = self.open(self.js_hashes_uri, headers={'Accept': 'application/json'}).json()
        app = self.app.go(hash=hashes['web-axb']['hash'])
        self.client_id = re.search(r'(?<=apigeeUrl:"https://api-banque.axa.fr",client_id:")\w+', app.text).group(0)

    def request_authorization(self):
        self.session.cookies.clear()
        self.logger.info('request authorization')
        url = self.build_authorization_uri()
        r = self.open(url)
        if r.status_code != 200:
            raise BrowserUnavailable("Cannot get pre-authorization")
        session_id = re.search('(?<=session_id=)[^&]+', r.url.split('#')[1]).group(0)
        data = {
            'access_code': self.username,
            'password': self.password,
            'space': 'PART'
        }
        r = self.open(self.AUTHORIZATION2_URI + '?session_id=' + session_id, data=data)
        if r.status_code != 200 or r.headers['Location'] is None:
            raise BrowserIncorrectPassword("Cannot get authorization")
        self.request_access_token(r.headers['Location'])
        self.open(self.check_user_uri, json={'espaceApplication':'PART'}, headers={'Content-Type': 'application/json;charset=utf-8', 'Accept': 'application/json'}).json()


    def code_challenge(self, verifier):
        digest = ''.join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~") for x in range(128))
        digest = base64.urlsafe_b64encode(bytes(digest, "utf8")).rstrip(b'=').decode('ascii')
        # override normal behavior !
        self.pkce_verifier = base64.b64encode(sha256(bytes(digest,"utf8")).digest()).decode('ascii')

        return digest

    def code_verifier(self, bytes_number=64):
        # override normal behavior !
        return None

    def build_authorization_parameters(self):
        params = super(AXABanque, self).build_authorization_parameters()
        params['error_uri'] = 'https://banque.axa.fr/auth/errorauthn'
        params['state'] = 'auth_' + ''.join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789") for x in range(25))
        params['response_type'] = 'code';
        return params

    def build_access_token_parameters(self, values):
        params = super(AXABanque, self).build_access_token_parameters(values)
        del params['client_secret']
        return params

    @need_login
    def iter_accounts(self):
        self.accounts.go(params={'types':'CHECKING', 'roles':'TIT,COT'}, headers={'Accept': 'application/json'})
        self.accounts = [acc for acc in self.page.iter_accounts()]
        for acc in self.accounts:
            self.balances.go(id_account=acc.id, headers={'Accept': 'application/json'})
            for b in self.page.iter_balances():
                if b._balance_type == self.BALANCE_TYPE:
                    acc.balance = b.balance
            yield acc

    @need_login
    def iter_history(self, account):
        start = datetime.now() - timedelta(days=365)
        self.transactions.go(id_account=account.id, params={'dateFrom': start.isoformat(timespec='seconds')+'Z'}, headers={'Accept': 'application/json'})
        return self.page.get_history()

class AXABanque0(AXABrowser, StatesMixin):
    BASEURL = 'https://www.axabanque.fr'
    STATE_DURATION = 5

    # Bank
    bank_accounts = URL(
        r'/transactionnel/client/liste-comptes.html',
        r'/transactionnel/client/liste-(?P<tab>.*).html',
        r'/webapp/axabanque/jsp/visionpatrimoniale/liste_panorama_.*\.faces',
        r'/webapp/axabanque/page\?code=(?P<code>\d+)',
        r'/webapp/axabanque/client/sso/connexion\?token=(?P<token>.*)',
        BankAccountsPage
    )
    iban_pdf = URL(r'https://www.axabanque.fr/webapp/axabanque/formulaire_AXA_Banque/.*\.pdf.*', IbanPage)
    cbttransactions = URL(r'/webapp/axabanque/jsp/detailCarteBleu.*.faces', CBTransactionsPage)
    transactions = URL(
        r'/webapp/axabanque/jsp/panorama.faces',
        r'/webapp/axabanque/jsp/visionpatrimoniale/panorama_.*\.faces',
        r'/webapp/axabanque/jsp/detail.*.faces',
        r'/webapp/axabanque/jsp/.*/detail.*.faces',
        TransactionsPage
    )
    unavailable = URL(
        r'/login_errors/indisponibilite.*',
        r'.*page-indisponible.html.*',
        r'.*erreur/erreurBanque.faces',
        r'http://www.axabanque.fr/message/maintenance.htm',
        r'http://www.axabanque.fr/webapp/axabanque/jsp/erreur/erreurIndispoPdf.faces',
        UnavailablePage
    )

    # Wealth
    wealth_accounts = URL(
        r'https://espaceclient.axa.fr/$',
        r'https://espaceclient.axa.fr/accueil.html',
        r'https://connexion.adis-assurances.com',
        WealthAccountsPage
    )
    investment = URL(r'https://espaceclient.axa.fr/.*content/ecc-popin-cards/savings/(\w+)/repartition', InvestmentPage)
    investment_monaxa = URL(r'https://monaxaweb-gp.axa.fr/MonAxa/Contrat/', InvestmentMonAxaPage)
    performance_monaxa = URL(r'https://monaxaweb-gp.axa.fr/MonAxa/ContratPerformance/', PerformanceMonAxaPage)
    history = URL(
        r'https://espaceclient.axa.fr/accueil/savings/savings/contract/_jcr_content.eccGetSavingsOperations.json',
        HistoryPage
    )
    history_investments = URL(
        r'https://espaceclient.axa.fr/accueil/savings/savings/contract/_jcr_content.eccGetSavingOperationDetail.json',
        HistoryInvestmentsPage
    )
    details = URL(
        r'https://espaceclient.axa.fr/.*accueil/savings/(\w+)/contract',
        r'https://espaceclient.axa.fr/#',
        AccountDetailsPage
    )
    lifeinsurance_iframe = URL(
        r'https://assurance-vie.axabanque.fr/Consultation/SituationContrat.aspx',
        r'https://assurance-vie.axabanque.fr/Consultation/HistoriqueOperations.aspx',
        LifeInsuranceIframe
    )

    # netfinca bourse
    bourse = URL(
        r'/transactionnel/client/homepage_bourseCAT.html',
        r'https://bourse.axabanque.fr/netfinca-titres/servlet/com.netfinca.*',
        BoursePage
    )
    bourse_history = URL(
        r'https://bourse.axabanque.fr/netfinca-titres/servlet/com.netfinca.frontcr.account.AccountHistory',
        BoursePage
    )

    # Transfer
    recipients = URL(r'/transactionnel/client/enregistrer-nouveau-beneficiaire.html', RecipientsPage)
    add_recipient = URL(r'/webapp/axabanque/jsp/beneficiaireSepa/saisieBeneficiaireSepaOTP.faces', AddRecipientPage)
    recipient_confirmation_page = URL(
        r'/webapp/axabanque/jsp/beneficiaireSepa/saisieBeneficiaireSepaOTP.faces',
        RecipientConfirmationPage
    )
    validate_transfer = URL(r'/webapp/axabanque/jsp/virementSepa/saisieVirementSepa.faces', ValidateTransferPage)
    # also displays recent single transfers
    register_transfer = URL(
        r'/transactionnel/client/virement.html',
        r'/webapp/axabanque/jsp/virementSepa/saisieVirementSepa.faces',
        RegisterTransferPage
    )
    confirm_transfer = URL('/webapp/axabanque/jsp/virementSepa/confirmationVirementSepa.faces', ConfirmTransferPage)
    profile_page = URL('/transactionnel/client/coordonnees.html', BankProfilePage)
    scheduled_transfers = URL(r'/transactionnel/client/virements-en-cours.html', ScheduledTransfersPage)
    scheduled_transfer_details = URL(
        r'/webapp/axabanque/jsp/virementSepa/virementEnCoursSepa.faces',
        ScheduledTransferDetailsPage
    )

    reload_state = None

    __states__ = ['reload_state']

    def load_state(self, state):
        # reload state for add recipient step only
        if state.get('reload_state'):
            super(AXABanque, self).load_state(state)
        self.reload_state = None

    def __init__(self, *args, **kwargs):
        super(AXABanque, self).__init__(*args, **kwargs)
        self.cache = {}
        self.cache['invs'] = {}
        self.weboob = kwargs['weboob']

    @need_login
    def iter_accounts(self):
        if 'accs' not in self.cache.keys():
            accounts = []
            ids = set()
            owner_name = self.get_profile().name.upper().split(' ', 1)[1]
            # Get accounts
            self.transactions.go()
            self.bank_accounts.go()
            # Ugly 3 loops : nav through all tabs and pages
            for tab in self.page.get_tabs():
                for page, page_args in self.bank_accounts.stay_or_go(tab=tab).get_pages(tab):
                    for a in page.get_list():
                        if a.id in ids:
                            # the "-comptes" page may return the same accounts as other pages, skip them
                            continue

                        # Some card are not deferred debit card, skip them
                        if a._is_debit_card:
                            continue

                        ids.add(a.id)

                        # The url giving life insurrance investments seems to be temporary.
                        # That's why we have to get them now
                        if a.type == a.TYPE_LIFE_INSURANCE:
                            self.cache['invs'][a.id] = list(self.open(a._url).page.iter_investment())
                        args = a._args
                        # Trying to get IBAN for checking accounts
                        if a.type == a.TYPE_CHECKING and 'paramCodeFamille' in args:
                            iban_params = {
                                'action': 'RIBCC',
                                'numCompte': args['paramNumCompte'],
                                'codeFamille': args['paramCodeFamille'],
                                'codeProduit': args['paramCodeProduit'],
                                'codeSousProduit': args['paramCodeSousProduit'],
                            }
                            try:
                                # This request redirect to the iban page, using http protocol, in which we parse the accounts' iban
                                # This usually works but sometimes we have a second redirection to an unavailable pdf page,
                                # even if the pdf page is accessible on the website using the exact same request
                                r = self.open('/webapp/axabanque/popupPDF', params=iban_params, allow_redirects=False)
                            except ClientError:
                                a.iban = NotAvailable
                            else:
                                iban_pdf_url = r.headers.get('location')
                                if iban_pdf_url:
                                    # Using https instead of http makes it sure to get the pdf if it's available
                                    # we replace 'http:' and not 'http' to avoid replacing if we are already using https
                                    iban_pdf_url = iban_pdf_url.replace('http:', 'https:')
                                    try:
                                        r = self.location(iban_pdf_url)
                                        a.iban = r.page.get_iban()
                                    except BrowserUnavailable:
                                        a.iban = NotAvailable
                                else:
                                    a.iban = NotAvailable

                        # Get parent account for card accounts
                        # The parent account must be created before the card account
                        if a.type == Account.TYPE_CARD:
                            label_id = re.search(r'(\d{4,})', a.label).group()
                            for p in accounts:
                                if label_id in p.label and p.type == Account.TYPE_CHECKING:
                                    a.parent = p
                                    break
                        # Need it to get accounts from tabs
                        a._tab, a._pargs, a._purl = tab, page_args, self.url
                        self.set_ownership(a, owner_name)
                        accounts.append(a)
            # Get investment accounts if there has
            self.wealth_accounts.go()
            if self.wealth_accounts.is_here():
                accounts.extend(list(self.page.iter_accounts()))
            else:
                # it probably didn't work, go back on a regular page to avoid being logged out
                self.transactions.go()

            self.cache['accs'] = accounts
            self.bank_accounts.go()
        return self.cache['accs']

    def set_ownership(self, account, owner_name):
        # Some accounts _owner attribute says 'MLLE PRENOM NOM1' or other
        # only 'NOM' while profile.name is 'MME PRENOM NOM1 NOM2' or 'MME PRENOM NOM'
        # It makes it pretty hard to determine precisely wether the owernship
        # should be OWNER or ATTORNEY. So we prefer set it to NotAvailable:
        # better no information than an inaccurate one.
        if not account.ownership:
            if account.parent and account.parent.ownership:
                account.ownership = account.parent.ownership
            elif re.search(
                r'(m|mr|me|mme|mlle|mle|ml)\.? (.*)\bou (m|mr|me|mme|mlle|mle|ml)\b(.*)',
                account._owner, re.IGNORECASE
            ):
                account.ownership = AccountOwnership.CO_OWNER
            elif all(n in account._owner for n in owner_name.split()):
                account.ownership = AccountOwnership.OWNER
            elif 'Mandat' in account.label:
                account.ownership = AccountOwnership.ATTORNEY
            else:
                account.ownership = NotAvailable

    @need_login
    def go_account_pages(self, account, action):
        # Default to "comptes"
        tab = account._tab
        if not hasattr(account, '_tab'):
            tab = "comptes"
        self.bank_accounts.go(tab=tab)
        args = account._args
        args['javax.faces.ViewState'] = self.page.get_view_state()

        # Nav for accounts in tab pages
        if (
            tab != "comptes"
            and hasattr(account, '_url')
            and hasattr(account, '_purl')
            and hasattr(account, '_pargs')
        ):
            self.location(account._purl, data=account._pargs)
            self.location(account._url, data=args)
            # Check if we are on the good tab
            if isinstance(self.page, TransactionsPage) and action:
                self.page.go_action(action)
        else:
            target = self.page.get_form_action(args['_form_name'])
            self.location(target, data=args)

    def go_wealth_pages(self, account):
        self.wealth_accounts.go()
        self.location(account.url)
        self.location(self.page.get_account_url(account.url))

    def get_netfinca_account(self, account):
        # Important: this part is controlled by modules/lcl/pages.py
        owner_name = self.get_profile().name.upper().split(' ', 1)[1]
        self.go_account_pages(account, None)
        self.page.open_market()
        self.page.open_market_next()
        self.page.open_iframe()
        for bourse_account in self.page.get_list(name=owner_name):
            self.logger.debug('iterating account %r', bourse_account)
            bourse_id = bourse_account.id.replace('bourse', '')
            if account.id.startswith(bourse_id):
                return bourse_account

    @need_login
    def iter_investment(self, account):
        self.transactions.go()
        if account._acctype == 'bank' and account.type in (Account.TYPE_PEA, Account.TYPE_MARKET):
            if 'Liquidités' in account.label:
                return [create_french_liquidity(account.balance)]

            account = self.get_netfinca_account(account)
            self.location(account._market_link)
            assert self.bourse.is_here()
            return self.page.iter_investment()

        if account.id not in self.cache['invs']:
            if account._acctype == 'bank' and account._hasinv:
                self.go_account_pages(account, 'investment')
            elif account._acctype == 'investment':
                self.go_wealth_pages(account)
                investment_url = self.page.get_investment_url()
                if not investment_url:
                    # Try to fetch iframe URL
                    iframe_url = self.page.get_iframe_url()
                    if iframe_url:
                        self.location(iframe_url)
                        invests = list(self.page.iter_investment())
                        performance_url = self.page.get_performance_url()
                        if performance_url:
                            self.location(performance_url)
                            for inv in invests:
                                self.page.fill_investment(obj=inv)
                        return invests
                    self.logger.warning('no investment link for account %s, returning empty', account)
                    # fake data, don't cache it
                    return []
                self.location(investment_url)
            self.cache['invs'][account.id] = list(self.page.iter_investment(currency=account.currency))
        return self.cache['invs'][account.id]

    @need_login
    def iter_history(self, account):
        if account.type == Account.TYPE_LOAN:
            return
        elif account.type == Account.TYPE_PEA:
            self.go_account_pages(account, "history")

            # go on netfinca page to get pea history
            acc = self.get_netfinca_account(account)
            self.location(acc._market_link)
            self.bourse_history.go()

            if 'Liquidités' not in account.label:
                self.page.go_history_filter(cash_filter="market")
            else:
                self.page.go_history_filter(cash_filter="liquidity")

            for tr in self.page.iter_history():
                yield tr
            return

        if account.type == Account.TYPE_LIFE_INSURANCE and account._acctype == "bank":
            if not self.lifeinsurance_iframe.is_here():
                self.location(account._url)
            self.page.go_to_history()

            # Pass account investments to try to get isin code for transaction investments
            if account.id in self.cache['invs']:
                for tr in self.page.iter_history(investments=self.cache['invs'][account.id]):
                    yield tr

        # Side investment's website
        if account._acctype == 'investment':
            '''
            Transactions are available 10 by 10 in a JSON.
            To access it, we need the account 'pid' and to increment
            'skip' for each transaction page until the JSON is empty.
            However, transactions are not always in the chronological order.
            '''
            self.go_wealth_pages(account)
            pid = self.page.get_pid()
            skip = 0
            if not pid:
                self.logger.warning('No pid available for account %s, transactions cannot be retrieved.', account.id)
                return

            transactions = []
            self.go_to_transactions(pid, skip)
            # Pagination:
            while self.page.has_operations():
                for tr in self.page.iter_history():
                    transactions.append(tr)
                skip += 10
                self.go_to_transactions(pid, skip)

            for tr in sorted_transactions(transactions):
                # Get investments for each transaction
                params = {
                    'oid': tr._oid,
                    'pid': pid,
                }
                self.history_investments.go(params=params)
                if self.page.has_investments():
                    tr.investments = list(self.page.iter_transaction_investments())
                yield tr

        # Main website without investments
        elif account._acctype == 'bank' and not account._hasinv and account.type != Account.TYPE_CARD:
            self.go_account_pages(account, 'history')

            if self.page.more_history():
                for tr in sorted_transactions(self.page.get_history()):
                    yield tr
        # Get deferred card history
        elif account._acctype == 'bank' and account.type == Account.TYPE_CARD:
            for tr in sorted_transactions(self.deferred_card_transactions(account)):
                if tr.date <= date.today():
                    yield tr

    def go_to_transactions(self, pid, skip):
        params = {
            'pid': pid,
            'skip': skip,
        }
        self.history.go(params=params)

    def deferred_card_transactions(self, account):
        summary_date = NotAvailable
        self.go_account_pages(account, 'history')

        if self.page.get_deferred_card_history():
            for tr in self.page.get_history():
                # only deferred card accounts are typed TYPE_CARD
                if tr.type == Transaction.TYPE_CARD:
                    tr.type = Transaction.TYPE_DEFERRED_CARD

                # set summary date for deferred card transactions
                if tr.type == Transaction.TYPE_CARD_SUMMARY:
                    summary_date = tr.date
                else:
                    if summary_date == tr.date:
                        # search if summary date is already given for the next month
                        tr.date = self.get_transaction_summary_date(tr)
                    else:
                        tr.bdate = tr.date
                        tr.date = summary_date

                if tr.date is not NotAvailable:
                    yield tr
                else:
                    """
                    Because axa is stupid, they don't know that their own shitty website doesn't give
                    summary date for the current month and they absolutely want coming transactions
                    without the real date of debit.
                    Search for the coming date ...
                    """
                    tr.date = self.get_month_last_working_day_date(tr)
                    yield tr

    def get_transaction_summary_date(self, tr):
        tr_next_month = tr.vdate + relativedelta(months=1)

        for summary in self.page.get_summary():
            if (summary.date.year == tr_next_month.year) and (summary.date.month == tr_next_month.month):
                return summary.date
        return NotAvailable

    def get_month_last_working_day_date(self, tr):
        # search for the last day of the month which is not Saturday or Sunday.
        if date.today().month != tr.vdate.month:
            last_day_month = tr.vdate + relativedelta(day=1, months=2) - relativedelta(days=1)
        else:
            last_day_month = tr.vdate + relativedelta(day=1, months=1) - relativedelta(days=1)

        if last_day_month.weekday() == 5:
            return last_day_month - relativedelta(days=1)
        elif last_day_month.weekday() == 6:
            return last_day_month - relativedelta(days=2)
        else:
            return last_day_month

    @need_login
    def iter_coming(self, account):
        if account._acctype == "bank" and account.type == Account.TYPE_CARD:
            for tr in self.deferred_card_transactions(account):
                # if date of summary is available, skip the variable summary
                if tr.date >= date.today() and tr.type != Transaction.TYPE_CARD_SUMMARY:
                    yield tr

    @need_login
    def iter_recipients(self, origin_account_id):
        seen = set()

        # go on recipient page to get external recipient ibans
        self.recipients.go()

        for iban in self.page.get_extenal_recipient_ibans():
            seen.add(iban)

        # some connections don't have transfer page like connections with pea accounts only
        try:
            # go on transfer page to get all accounts transfer possible
            self.register_transfer.go()
        except HTTPNotFound:
            return

        if self.page.is_transfer_account(acc_id=origin_account_id):
            self.page.set_account(acc_id=origin_account_id)

            for recipient in self.page.get_recipients():
                if recipient.iban in seen:
                    recipient.category = 'Externe'
                yield recipient

    def copy_recipient_obj(self, recipient):
        rcpt = Recipient()
        rcpt.id = recipient.iban
        rcpt.iban = recipient.iban
        rcpt.label = recipient.label
        rcpt.category = 'Externe'
        rcpt.enabled_at = date.today()
        rcpt.currency = 'EUR'
        return rcpt

    @need_login
    def new_recipient(self, recipient, **params):
        if 'code' in params:
            self.page.send_code(params['code'])
            return self.rcpt_after_sms(recipient)

        self.recipients.go()
        self.page.go_add_new_recipient_page()

        if self.recipient_confirmation_page.is_here():
            # Confirm that user want to add recipient
            self.page.continue_new_recipient()

        if self.recipient_confirmation_page.is_here():
            self.page.check_errors()

        assert self.add_recipient.is_here()
        self.page.set_new_recipient_iban(recipient.iban)
        rcpt = self.copy_recipient_obj(recipient)
        # This send the sms to user
        self.page.set_new_recipient_label(recipient.label)

        raise AddRecipientStep(rcpt, Value('code', label='Veuillez entrer le code reçu par SMS.'))

    @need_login
    def rcpt_after_sms(self, recipient):
        assert self.page.is_add_recipient_confirmation()
        self.recipients.go()
        return self.page.get_rcpt_after_sms(recipient)

    @need_login
    def init_transfer(self, account, recipient, amount, reason, exec_date):
        if exec_date == date.today():
            # Avoid to chose deferred transfer
            exec_date = None

        self.register_transfer.go()
        self.page.set_account(account.id)
        self.page.fill_transfer_form(account.id, recipient.iban, amount, reason, exec_date)
        return self.page.handle_response(account, recipient, amount, reason)

    @need_login
    def execute_transfer(self, transfer, **params):
        self.page.validate_transfer(self.password)
        return transfer

    @need_login
    def get_subscription_list(self):
        raise NotImplementedError()

    @need_login
    def iter_documents(self, subscription):
        raise NotImplementedError()

    @need_login
    def download_document(self, url):
        raise NotImplementedError()

    @need_login
    def get_profile(self):
        # Error 500 on 3 lines here :
        #self.profile_page.go()
        action_needed_message = False #self.page.renew_personal_information()
        if action_needed_message:
            # In order to fetch the customer's information, personal data
            # must be updated on the website
            raise ActionNeeded(action_needed_message)

        profile = Person() # self.page.get_profile()
        self.bank_accounts.go()
        profile.name = self.page.get_profile_name()
        return profile

    @need_login
    def iter_emitters(self):
        self.register_transfer.go()
        return self.page.iter_emitters()

    def _get_recipients_by_emitter(self):
        recipients_by_emitter = []
        for emitter in self.iter_emitters():
            self.page.set_account(emitter.id)
            recipients = []
            for recipient in self.page.get_recipients():
                recipients.append(recipient)
            recipients_by_emitter.append((emitter, recipients,))
        return recipients_by_emitter

    @staticmethod
    def _find_emitter_and_recipient_from_recipient_name(recipients_by_emitters, recipient_name):
        # find more information on non deferred single transfers
        # in this case, the only information we have about recipient is a part of its label
        matching_recipients = []
        for emitter, recipients in recipients_by_emitters:
            for recipient in recipients:
                # a recipient label is constructed using the following syntax:
                # <recipient account name> - <recipient name>
                # the transfer list only provides the recipient name
                # we will base our matching on it
                if recipient.label.endswith(' - {}'.format(recipient_name)):
                    matching_recipients.append((emitter, recipient,))
        # information is valuable only if one recipient from one account matched the recipient name
        # otherwise, we won't know which emitter and recipient to use
        if len(matching_recipients) == 1:
            return matching_recipients[0]

    @need_login
    def iter_transfers(self, account):
        self.scheduled_transfers.go()
        for transfer in self.page.iter_transfers():
            if not account or account.iban == transfer.account_iban:
                transfer_page = self.page.open_transfer_page(transfer._unparsed_js_args)
                # will get additional information on the transfer's detailed page
                transfer_page.fill_scheduled_transfer(obj=transfer)
                yield transfer

        # now the awful part, single non deferred transfers displayed with very few information
        self.register_transfer.go()
        recipients_by_emitter = self._get_recipients_by_emitter()
        # Very few information on immediate transfers. In order to retrieve something, a matching is done
        # between recipient name given with transfer and the recipient, and since the recipient is linked to an
        # emitter account, information about the emitter are found.
        for transfer in self.page.iter_transfers():
            matched_information = self._find_emitter_and_recipient_from_recipient_name(
                recipients_by_emitter,
                transfer._recipient_name
            )
            # keep in mind that if we were not able to find an emitter,
            # there is no way to filter with the account parameter
            if matched_information:
                emitter, recipient = matched_information
                # not 100% efficient, because children accounts can have the same beginning of id.
                # but we don't have to use the heavy iter_accounts
                if account and not account.id.startswith(emitter.id):
                    continue
                transfer.recipient_iban = recipient.iban
                transfer.recipient_label = recipient.label
                transfer.account_label = emitter.label
            yield transfer


class AXAAssurance(AXABrowser):
    BASEURL = 'https://espaceclient.axa.fr'

    accounts = URL(r'/accueil.html', WealthAccountsPage)
    history = URL(r'/accueil/savings/savings/contract/_jcr_content.eccGetSavingsOperations.json', HistoryPage)
    history_investments = URL(
        r'/accueil/savings/savings/contract/_jcr_content.eccGetSavingOperationDetail.json',
        HistoryInvestmentsPage
    )
    details = URL(
        r'.*accueil/savings/(\w+)/contract',
        r'/#',
        AccountDetailsPage
    )

    investment = URL(r'/content/ecc-popin-cards/savings/[^/]+/repartition', InvestmentPage)
    investment_monaxa = URL(r'https://monaxaweb-gp.axa.fr/MonAxa/Contrat/', InvestmentMonAxaPage)
    performance_monaxa = URL(r'https://monaxaweb-gp.axa.fr/MonAxa/ContratPerformance/', PerformanceMonAxaPage)

    documents_life_insurance = URL(
        r'/content/espace-client/accueil/mes-documents/situations-de-contrats-assurance-vie.content-inner.din_SAVINGS_STATEMENT.html',
        DocumentsPage
    )
    documents_certificates = URL(
        r'/content/espace-client/accueil/mes-documents/attestations-d-assurances.content-inner.din_CERTIFICATE.html',
        DocumentsPage
    )
    documents_tax_area = URL(
        r'https://espaceclient.axa.fr/content/espace-client/accueil/mes-documents/espace-fiscal.content-inner.din_TAX.html',
        DocumentsPage
    )
    documents_membership_fee = URL(
        r'/content/espace-client/accueil/mes-documents/avis-d-echeance.content-inner.din_PREMIUM_STATEMENT.html',
        DocumentsPage
    )

    download = URL(
        r'/content/ecc-popin-cards/technical/detailed/download-document.downloadPdf.html',
        DownloadPage
    )
    profile = URL(r'/content/ecc-popin-cards/transverse/userprofile.content-inner.html\?_=\d+', ProfilePage)

    def __init__(self, *args, **kwargs):
        super(AXAAssurance, self).__init__(*args, **kwargs)

    def go_wealth_pages(self, account):
        self.location('/' + account.url)
        self.location(self.page.get_account_url(account.url))

    @need_login
    def iter_accounts(self):
        self.accounts.go()
        return self.page.iter_accounts()

    @need_login
    def iter_investment_espaceclient(self, account):
        invests = []
        portfolio_page = self.page
        detailed_view = self.page.detailed_view()
        if detailed_view:
            self.location(detailed_view)
            invests.extend(self.page.iter_investment(currency=account.currency))
        for inv in portfolio_page.iter_investment(currency=account.currency):
            i = [i2 for i2 in invests if
                 (i2.valuation == inv.valuation and i2.label == inv.label)]
            assert len(i) in (0, 1)
            if i:
                i[0].portfolio_share = inv.portfolio_share
            else:
                invests.append(inv)
        return invests

    @need_login
    def iter_investment_monaxa(self, account):
        # Try to fetch a URL to 'monaxaweb-gp.axa.fr'
        invests = list(self.page.iter_investment())

        performance_url = self.page.get_performance_url()
        if performance_url:
            self.location(performance_url)
            for inv in invests:
                self.page.fill_investment(obj=inv)

        # return to espaceclient.axa.fr
        self.accounts.go()
        return invests

    @need_login
    def iter_investment(self, account):
        self.go_wealth_pages(account)

        investment_url = self.page.get_investment_url()
        if investment_url:
            self.location(investment_url)
            return self.iter_investment_espaceclient(account)

        iframe_url = self.page.get_iframe_url()
        if iframe_url:
            # Set correct Referer to avoid 302 followed by 406 errors (Not Acceptable)
            headers = {'Referer': 'https://espaceclient.axa.fr/accueil.html'}
            self.location(iframe_url, headers=headers)
            return self.iter_investment_monaxa(account)

        # No data available for this account.
        self.logger.warning('No investment URL available for account %s, investments cannot be retrieved.', account.id)
        return []

    @need_login
    def iter_history(self, account):
        '''
        Transactions are available 10 by 10 in a JSON.
        To access it, we need the account 'pid' and to increment
        'skip' for each transaction page until the JSON is empty.
        However, transactions are not always in the chronological order.
        '''
        self.go_wealth_pages(account)
        pid = self.page.get_pid()
        skip = 0
        if not pid:
            self.logger.warning('No pid available for account %s, transactions cannot be retrieved.', account.id)
            return

        transactions = []
        self.go_to_transactions(pid, skip)
        # Pagination:
        while self.page.has_operations():
            for tr in self.page.iter_history():
                transactions.append(tr)
            skip += 10
            self.go_to_transactions(pid, skip)

        for tr in sorted_transactions(transactions):
            # Get investments for each transaction
            params = {
                'oid': tr._oid,
                'pid': pid,
            }
            self.history_investments.go(params=params)
            if self.page.has_investments():
                tr.investments = list(self.page.iter_transaction_investments())
            else:
                tr.investments = []
            yield tr

    def go_to_transactions(self, pid, skip):
        params = {
            'pid': pid,
            'skip': skip,
        }
        self.history.go(params=params)

    def iter_coming(self, account):
        raise NotImplementedError()

    @need_login
    def get_subscription_list(self):
        sub = Subscription()
        sub.label = sub.id = self.username
        yield sub

    @need_login
    def iter_documents(self, subscription):
        document_urls = [
            self.documents_life_insurance,
            self.documents_certificates,
            self.documents_tax_area,
            self.documents_membership_fee,
        ]
        for url in document_urls:
            url.go()
            for doc in self.page.get_documents(subid=subscription.id):
                yield doc

    @need_login
    def download_document(self, download_id):
        self.download.go(data={'documentId': download_id})
        return self.page.content

    @need_login
    def get_profile(self):
        self.profile.go()
        return self.page.get_profile()
