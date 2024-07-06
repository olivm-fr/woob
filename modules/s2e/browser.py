# Copyright(C) 2016      Edouard Lambert
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

# flake8: compatible

import re

from requests.exceptions import ConnectionError
from urllib3.exceptions import ReadTimeoutError

from woob.browser import LoginBrowser, URL, need_login, StatesMixin
from woob.browser.exceptions import ServerError, HTTPNotFound
from woob.exceptions import (
    BrowserIncorrectPassword, ActionNeeded, BrowserUnavailable,
    NeedInteractiveFor2FA, SentOTPQuestion,
)
from woob.capabilities.bank import Investment, NoAccountsException
from woob.tools.value import Value
from woob.tools.capabilities.bank.investments import is_isin_valid
from woob.tools.decorators import retry

from .pages import (
    LoginPage, AccountsPage, AMFHSBCPage, AmundiPage, AMFSGPage, HistoryPage, ErrorPage,
    LyxorfcpePage, EcofiPage, EcofiDummyPage, LandingPage, SwissLifePage, LoginErrorPage,
    EtoileGestionPage, EtoileGestionCharacteristicsPage, EtoileGestionDetailsPage,
    BNPInvestmentsPage, BNPInvestmentDetailsPage, LyxorFundsPage, EsaliaDetailsPage,
    EsaliaPerformancePage, AmundiDetailsPage, AmundiPerformancePage, ProfilePage,
    HsbcVideoPage, CprInvestmentPage, CprPerformancePage, CmCicInvestmentPage,
    HsbcInvestmentPage, EServicePage, HsbcTokenPage, AccountsInfoPage, StockOptionsPage,
    TemporarilyUnavailablePage, SetCookiePage, CreditdunordPeePage,
)


class S2eBrowser(LoginBrowser, StatesMixin):
    login = URL(
        r'/portal/salarie-(?P<slug>\w+)/authentification',
        r'(.*)portal/salarie-(?P<slug>\w+)/authentification',
        r'/portal/j_security_check', LoginPage
    )
    login_error = URL(r'/portal/login', LoginErrorPage)
    logout = URL(r'/portal/salarie-(?P<slug>\w+)/\?portal:action=Logout&portal:componentId=UIPortal')
    temporarily_unavailable = URL(r'/pagesdedelestage/(\w+)/index.html', TemporarilyUnavailablePage)
    set_cookies = URL(r'/portal/setcontrolcookie', SetCookiePage)
    landing = URL(r'(.*)portal/salarie-bnp/accueil', LandingPage)
    accounts = URL(
        r'/portal/salarie-(?P<slug>\w+)/monepargne/mesavoirs\?language=(?P<lang>)',
        r'/portal/salarie-(?P<slug>\w+)/monepargne/mesavoirs',
        AccountsPage
    )
    accounts_info = URL(r'/portal/salarie-(?P<slug>\w+)/monepargne/mesdispositifs', AccountsInfoPage)
    stock_options = URL(r'/portal/salarie-(?P<slug>\w+)/monepargne/mesblocages', StockOptionsPage)
    history = URL(r'/portal/salarie-(?P<slug>\w+)/operations/consulteroperations', HistoryPage)
    error = URL(r'/maintenance/.+/', ErrorPage)
    profile = URL(
        r'/portal/salarie-(?P<slug>\w+)/mesdonnees/coordperso\?scenario=ConsulterCP',
        r'/portal/salarie-(?P<slug>\w+)/mesdonnees/coordperso\?scenario=ConsulterCP&language=(?P<lang>)',
        ProfilePage
    )
    # Amundi pages
    isincode_amundi = URL(r'https://www.amundi-ee.com/entr/product', AmundiPage)
    performance_details = URL(r'https://www.amundi-ee.com/entr/ezjscore/call(.*)_tab_2', AmundiPerformancePage)
    investment_details = URL(r'https://www.amundi-ee.com/entr/ezjscore/call(.*)_tab_5', AmundiDetailsPage)
    # SG Gestion pages
    amfcode_sg = URL(r'http://sggestion-ede.com/product', AMFSGPage)
    # Ecofi pages
    isincode_ecofi = URL(r'http://www.ecofi.fr/fr/fonds/.*#yes\?bypass=clientprive', EcofiPage)
    pdf_file_ecofi = URL(r'http://www.ecofi.fr/sites/.*', EcofiDummyPage)
    # Lyxor pages
    lyxorfcpe = URL(r'http://www.lyxorfcpe.com/part', LyxorfcpePage)
    lyxorfunds = URL(r'https://www.lyxorfunds.com', LyxorFundsPage)
    # Swisslife pages
    swisslife = URL(r'http://fr.swisslife-am.com/fr/produits/.*', SwissLifePage)
    # Etoile Gestion pages
    etoile_gestion = URL(r'https?://www.etoile-gestion.com/index.php/etg_fr_fr/productsheet/view/.*', EtoileGestionPage)
    etoile_gestion_characteristics = URL(
        r'https?://www.etoile-gestion.com/etg_fr_fr/ezjscore/.*',
        EtoileGestionCharacteristicsPage
    )
    etoile_gestion_details = URL(r'https?://www.etoile-gestion.com/productsheet/.*', EtoileGestionDetailsPage)
    # BNP pages
    bnp_investments = URL(
        r'https://optimisermon.epargne-retraite-entreprises.bnpparibas.com',
        BNPInvestmentsPage
    )
    # Unused for the moment but this URL has to be handled to avoid the module thinking
    # we're not logged in after calling investments due to "personeo.erpagne..." being not matched
    new_bnp_investments = URL(
        r'https://personeo.epargne-retraite-entreprises.bnpparibas.com/portal/salarie-bnp',
        BNPInvestmentsPage
    )
    bnp_investment_details = URL(
        r'https://funds-api.bnpparibas.com/api/performances/(?P<id>\w+)',
        BNPInvestmentDetailsPage
    )
    # Esalia pages
    esalia_details = URL(r'https://www.societegeneralegestion.fr/psSGGestionEntr/productsheet/view', EsaliaDetailsPage)
    esalia_performance = URL(
        r'https://www.societegeneralegestion.fr/psSGGestionEntr/ezjscore/call(.*)_tab_2',
        EsaliaPerformancePage
    )
    # HSBC pages
    hsbc_video = URL(r'https://(.*)videos-pedagogiques/fonds-hsbc-', HsbcVideoPage)
    hsbc_token_page = URL(r'https://www.epargne-salariale-retraite.hsbc.fr/api/v1/token/issue', HsbcTokenPage)
    amfcode_search_hsbc = URL(r'https://www.epargne-salariale-retraite.hsbc.fr/api/v1/nav/funds', AMFHSBCPage)
    amfcode_hsbc = URL(r'https://www.epargne-salariale-retraite.hsbc.fr/api/v1/detail/primary-identifier', AMFHSBCPage)
    hsbc_investments = URL(
        r'https://www.epargne-salariale-retraite.hsbc.fr/fr/epargnants/fund-centre',
        r'https://www.assetmanagement.hsbc.com/fr/fcpe-closed',
        r'https://www.assetmanagement.hsbc.com/fr/fcpe-open',
        r'https://www.epargne-salariale-retraite.hsbc.fr/fr/epargnants/fund-centre/priv/(?P<fund_id>.*)',
        HsbcInvestmentPage
    )
    # CPR Asset Management pages
    cpr_investments = URL(r'https://www.cpr-am.fr/particuliers/product/view', CprInvestmentPage)
    cpr_performance = URL(r'https://www.cpr-am.fr/particuliers/ezjscore', CprPerformancePage)
    # CreditMutuel-AM (Former: CM-CIC) investments
    cm_cic_investments = URL(
        r'https://www.creditmutuel-am.eu/fr/particuliers/nos-fonds/VALE_FicheSynthese.aspx',
        r'https://www.creditmutuel-am.eu/fr/particuliers/nos-fonds/VALE_Fiche',
        r'https://www.cmcic-am.fr/fr/particuliers/nos-fonds/VALE_FicheSynthese.aspx',
        r'https://www.cmcic-am.fr/fr/particuliers/nos-fonds/VALE_Fiche',
        CmCicInvestmentPage
    )

    e_service_page = URL(
        r'/portal/salarie-(?P<slug>\w+)/documents/eservice',
        EServicePage,
    )

    STATE_DURATION = 10
    TIMEOUT = 60

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
        self.is_interactive = self.config.get('request_information', Value()).get() is not None

        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()

        r''' All abstract modules have a regex on the password (such as '\d{6}'), except
        'bnppere' because the Visiogo browser accepts non-digital passwords, since
        there is no virtual keyboard on the visiogo website. Instead of crashing, it
        sometimes works to extract the digits from the input and try to login if the original
        input contains exactly 6 digits. '''
        if not str.isdigit(str(kwargs['password'])):
            digital_password = re.sub(r'[^0-9]', '', kwargs['password'])
            if len(digital_password) != 6:
                # No need to try to login, it will fail
                raise BrowserIncorrectPassword()
            # Try the 6 extracted digits as password
            kwargs['password'] = digital_password

        self.secret = None
        if 'secret' in self.config:
            self.secret = self.config['secret'].get()

        super(S2eBrowser, self).__init__(*args, **kwargs)
        self.cache = {}
        self.cache['invs'] = {}
        self.cache['pockets'] = {}
        self.cache['details'] = {}

    def dump_state(self):
        state = super(S2eBrowser, self).dump_state()
        state.pop('url', None)  # after deinit, we get LoggedOut exception on the next sync by trying to load the url from the state.
        return state

    def deinit(self):
        if self.page and self.page.logged:
            self.logout.go(slug=self.SLUG)
        super(S2eBrowser, self).deinit()

    @retry((BrowserUnavailable), tries=4)
    def initiate_login_page(self):
        # It looks like that there are transitive issues for loading the login
        # page, so we retry send_login at least once.
        try:
            self.login.go(slug=self.SLUG)
        except HTTPNotFound as error:
            if error.response.status_code == 404 and self.set_cookies.match(error.response.url):
                # sometimes we get redirected here, a retry is enough. that's why we raise BrowserUnavailable
                raise BrowserUnavailable()
            raise

        if self.temporarily_unavailable.is_here():
            # a retry should solve this
            raise BrowserUnavailable(self.page.get_unavailability_message())

        assert self.login.is_here(), 'We are not on the expected login page'
        self.page.check_error()

    def send_login(self):
        self.initiate_login_page()
        if not self.page.is_login_form_available() and not self.page.is_otp_form_available():
            # If the login form cannot be found, we re-initiate the login page.
            self.initiate_login_page()
            assert self.page.is_login_form_available(), 'Unable to find the login form during login.'

        if not self.page.is_otp_form_available():
            self.page.login(self.username, self.password, self.secret)

        # check whether to send OTP
        if self.login.is_here():
            form = self.page.get_form_send_otp()
            if form:
                if not self.is_interactive:
                    raise NeedInteractiveFor2FA
                else:
                    form.submit()
                    raise SentOTPQuestion(
                        'otp',
                        message='Veuillez saisir votre code de sécurité (reçu par mail ou par sms)',
                    )

    def handle_otp(self, otp):
        self.page.check_error()
        self.page.send_otp(otp)
        if self.login.is_here():
            self.page.check_error()

    def do_login(self):
        otp = None
        if 'otp' in self.config:
            otp = self.config['otp'].get()

        if self.login.is_here() and otp:
            self.handle_otp(otp)
        else:
            self.send_login()
            if self.login.is_here() and otp:
                self.handle_otp(otp)

            if self.login_error.is_here():
                raise BrowserIncorrectPassword()
            if self.login.is_here():
                error = self.page.get_error()
                if error:
                    raise ActionNeeded(error)

    @need_login
    def iter_accounts(self):
        if 'accs' not in self.cache.keys():
            tab_changed = False
            no_accounts_message = None
            self.accounts.go(slug=self.SLUG, lang=self.LANG)
            # weird wrongpass
            if not self.accounts.is_here():
                raise BrowserIncorrectPassword()

            # Handle multi entreprise accounts
            multi_space = self.page.get_multi()
            if not multi_space:
                multi_space = [None]

            accs = []
            for space in multi_space:
                if space is not None:
                    self.page.go_multi(space)

                self.accounts_info.go(slug=self.SLUG)
                # since IDs are not available anymore on AccountPage
                # I retrieve all those accounts information here.
                accounts_info = self.page.get_account_info()
                company_name = self.profile.go(slug=self.SLUG).get_company_name()
                self.accounts.go(slug=self.SLUG, lang=self.LANG)
                no_accounts_in_space_message = self.page.get_no_accounts_message()
                if no_accounts_in_space_message:
                    if not no_accounts_message:
                        no_accounts_message = no_accounts_in_space_message
                    continue

                # If no accounts are available or the website unavailable
                # there won't be any form on page and cause a bug
                if not tab_changed and self.page.has_form():
                    # force the page to be on the good tab the first time
                    self.page.change_tab('account')
                    tab_changed = True

                space_accs = []
                seen_account_ids = []
                for account in self.page.iter_accounts():
                    self.page.fill_account(
                        obj=account,
                        account_info=accounts_info,
                        seen_account_ids=seen_account_ids,
                        company_name=company_name,
                        space=space,
                        # Can't use an existing Field from account obj, so pass the "label" as Env
                        label=account.label
                    )
                    if account.id:
                        seen_account_ids.append(account.id)
                        # in order to associate properly the accounts with their account ids,
                        # we need to get all the accounts and filter them after.
                        if account.balance:
                            space_accs.append(account)
                # each space can have multiple accounts
                # for each account we will add the attribute _len_space_accs
                # which is the number of accounts in the current space.
                # (if a space has 1 account then account._len_space_accs=1)
                # this will be helpful in iter_history, since we know that all transactions
                # belong to a unique account, we won't need to visit the details page.
                len_space_accs = len(space_accs)
                for account in space_accs:
                    account._len_space_accs = len_space_accs
                accs.extend(space_accs)

            if not accs:
                if no_accounts_message:
                    # Accounts list is empty and we found the
                    # message on at least one of the spaces:
                    raise NoAccountsException(no_accounts_message)

                # Some accounts are bugged and the website displays an error message
                error_message = self.page.get_error_message()
                if error_message:
                    raise BrowserUnavailable()
            self.cache['accs'] = accs
        return self.cache['accs']

    @need_login
    def iter_investment(self, account):
        if account.id not in self.cache['invs']:
            self.accounts.go(slug=self.SLUG)
            # Handle multi entreprise accounts
            if account._space:
                self.page.go_multi(account._space)
                self.accounts.go(slug=self.SLUG)
            # Select account
            # force the page to be on the good tab
            self.page.change_tab('investment')
            self.page.get_investment_pages(account.id)
            investments_without_quantity = [i for i in self.page.iter_investment()]
            # Get page with quantity
            self.page.get_investment_pages(account.id, valuation=False)
            investments_without_performances = self.page.update_invs_quantity(investments_without_quantity)
            investments = self.update_investments(investments_without_performances)
            self.cache['invs'][account.id] = investments
        return self.cache['invs'][account.id]

    @need_login
    def update_investments(self, investments):
        for inv in investments:
            if inv._link:
                if self.bnp_investments.match(inv._link):
                    # Although we don't fetch anything on BNPInvestmentsPage, this request is
                    # necessary otherwise the calls to the BNP API will return a 401 error
                    try:
                        self.location(inv._link, timeout=30)
                    except (ServerError, ConnectionError):
                        # For some connections, this request returns a 503 even on the website
                        # Timeout is set at 60 but on the website this can take up to ~120
                        # seconds before the website answers with a 503. Retrying three times
                        # with a timeout at 60 would make a synchronization a bit too long,
                        # reducing it to 30 might be more acceptable.
                        self.logger.warning('Server returned a Server Error when trying to fetch investment performances.')
                        continue

                    if not self.bnp_investments.match(self.url):
                        # BNPInvestmentsPage was not accessible, trying the next request
                        # would lead to a 401 error. This happens utterly randomly
                        # but this can be detected if inv._link is redirecting us to
                        # https://personeo.epargne-retraite-entreprises.bnpparibas.com/portal/salarie-bnp
                        # rather than https://optimisermon.epargne-retraite-entreprises.bnpparibas.com
                        self.logger.warning('Could not access BNP investments page, no investment details will be fetched.')
                        continue

                    # Access the BNP API to get the investment details using its ID (found in its label)
                    m = re.search(r'- (\d+)$', inv.label)
                    if m:
                        inv_id = m.group(1)
                        try:
                            self.bnp_investment_details.go(id=inv_id)
                        except (ConnectionError, ReadTimeoutError):
                            # The BNP API times out quite often so we must handle timeout errors
                            self.logger.warning('Could not connect to the BNP API, no investment details will be fetched.')
                            continue
                        else:
                            if not self.bnp_investment_details.is_here():
                                self.logger.warning('We got redirected when going to bnp_investment_details')
                            elif self.page.is_content_valid():
                                self.page.fill_investment(obj=inv)
                            else:
                                self.logger.warning('Empty page on BNP API, no investment details will be fetched.')
                    else:
                        self.logger.warning('Could not fetch BNP investment ID in its label, no investment details will be fetched.')

                elif self.isincode_amundi.match(inv._link):
                    try:
                        self.location(inv._link)
                    except HTTPNotFound:
                        self.logger.warning('Details on ISIN Amundi page are not available for this investment.')
                        continue
                    details_url = self.page.get_details_url()
                    performance_url = self.page.get_performance_url()
                    if details_url:
                        if 'None' in details_url:
                            self.logger.warning('Invest %s skipped, investment details is unavaible', inv.code)
                            continue
                        self.location(details_url)
                        if self.investment_details.is_here():
                            inv.recommended_period = self.page.get_recommended_period()
                            inv.asset_category = self.page.get_asset_category()
                    if performance_url:
                        self.location(performance_url)
                        if self.performance_details.is_here():
                            inv.performance_history = self.page.get_performance_history()

                elif self.amfcode_sg.match(inv._link) or self.lyxorfunds.match(inv._link):
                    # SGgestion-ede or Lyxor investments: not all of them have available attributes.
                    # For those requests to work in every case we need the headers from AccountsPage
                    self.location(inv._link, headers={'Referer': self.accounts.build(slug=self.SLUG)})
                    self.page.fill_investment(obj=inv)

                elif self.esalia_details.match(inv._link):
                    # Esalia (Société Générale Épargne Salariale) details page:
                    # Fetch code, code_type & asset_category here
                    m = re.search(r'idvm\/(.*)\/lg', inv._link)
                    if m:
                        if is_isin_valid(m.group(1)):
                            inv.code = m.group(1)
                            inv.code_type = Investment.CODE_TYPE_ISIN
                    self.location(inv._link)
                    inv.asset_category = self.page.get_asset_category()
                    # Fetch performance_history if available URL
                    performance_url = self.page.get_performance_url()
                    if performance_url:
                        self.location('https://www.societegeneralegestion.fr' + performance_url)
                        inv.performance_history = self.page.get_performance_history()

                elif self.etoile_gestion_details.match(inv._link):
                    # Etoile Gestion investments details page:
                    # Fetch asset_category & performance_history
                    self.location(inv._link)
                    inv.asset_category = self.page.get_asset_category()
                    performance_url = self.page.get_performance_url()
                    if performance_url:
                        self.location(performance_url)
                        if self.etoile_gestion_characteristics.is_here():
                            inv.performance_history = self.page.get_performance_history()

                elif self.cpr_investments.match(inv._link):
                    self.location(inv._link)
                    self.page.fill_investment(obj=inv)
                    # Fetch all performances on the details page
                    performance_url = self.page.get_performance_url()
                    if performance_url:
                        self.location(performance_url)
                        complete_performance_history = self.page.get_performance_history()
                        if complete_performance_history:
                            inv.performance_history = complete_performance_history

                elif self.hsbc_investments.match(inv._link):
                    # Handle investment detail as for erehsbc subsite
                    m = re.search(r'id=(\w+).+SH=([\w\-]+)', inv._link)
                    if m:
                        fund_id = m.group(1)
                        share_class = m.group(2)
                        if "/fcpe-closed" in inv._link:
                            # This are non public funds, so they are not visible on search engine.
                            self.hsbc_investments.go(fund_id=fund_id)
                            hsbc_params = self.page.get_params()
                            share_id = hsbc_params['pageInformation']['shareId']

                            # Code to perform an api request, that might be needed
                            # to retrieve performance or other info, but so far
                            # we can get the AMF code directly from hsbc_params without
                            # furter requests
                            '''
                            hsbc_token_id = hsbc_params['pageInformation']['primaryIdentifierUrl']['id']
                            self.hsbc_token_page.go(
                                headers={
                                    'X-Component': hsbc_token_id,
                                    'X-Country': 'FR',
                                    'X-Language': 'FR',
                                },
                                method='POST',
                            )
                            hsbc_params['currency'] = '0P00012TPV'
                            hsbc_params['exchange'] = "Not Applicable"
                            hsbc_params['performance'] = "EUR"
                            import uuid
                            hsbc_params['id'] = "{%s}" % uuid.uuid4()

                            hsbc_token = self.page.text
                            self.amfcode_hsbc.go(
                                headers={'Authorization': 'Bearer %s' % hsbc_token},
                                json=hsbc_params,
                            )
                            inv.code = self.page.get_code()
                            '''
                            code = share_id
                        else:
                            self.hsbc_investments.go()
                            hsbc_params = self.page.get_params()
                            hsbc_token_id = hsbc_params['pageInformation']['dataUrl']['id']
                            self.hsbc_token_page.go(
                                headers={
                                    'X-Component': hsbc_token_id,
                                    'X-Country': 'FR',
                                    'X-Language': 'FR',
                                },
                                method='POST',
                            )

                            hsbc_token = self.page.text
                            hsbc_params['paging'] = {'currentPage': 1}
                            hsbc_params['searchTerm'] = [fund_id]
                            hsbc_params['view'] = 'Prices'
                            hsbc_params['appliedFilters'] = []
                            self.amfcode_search_hsbc.go(
                                headers={'Authorization': 'Bearer %s' % hsbc_token},
                                json=hsbc_params,
                            )
                            code = self.page.get_code_from_search_result(share_class)
                        inv.code = code
                        inv.code_type = Investment.CODE_TYPE_AMF

                elif self.cm_cic_investments.match(inv._link):
                    self.location(inv._link)
                    if self.cm_cic_investments.is_here():
                        # Load investment details data
                        params = {
                            'ddp': self.page.get_ddp(),
                            'forceActualisation': 'O',
                        }
                        self.cm_cic_investments.go(params=params)
                        inv.code = self.page.get_code()
                        inv.code_type = Investment.CODE_TYPE_AMF
                        inv.performance_history = self.page.get_performance_history()

        return investments

    @need_login
    def iter_pocket(self, account):
        if account.id not in self.cache['pockets']:
            self.iter_investment(account)
            # Select account
            self.accounts.go(slug=self.SLUG)
            # force the page to be on the good tab
            self.page.change_tab('pocket')
            self.page.get_investment_pages(account.id, pocket=True)
            pockets = [p for p in self.page.iter_pocket(accid=account.id)]
            # Get page with quantity
            self.page.get_investment_pages(account.id, valuation=False, pocket=True)
            self.cache['pockets'][account.id] = self.page.update_pockets_quantity(pockets)
        return self.cache['pockets'][account.id]

    @need_login
    def iter_history(self, account):
        self.history.go(slug=self.SLUG)
        # Handle multi entreprise accounts
        if account._space:
            self.page.go_multi(account._space)
            self.history.go(slug=self.SLUG)
        # Get more transactions on each page
        if self.page.show_more("50"):
            for tr in self.page.iter_history(accid=account.id, len_space_accs=account._len_space_accs):
                yield tr

    @need_login
    def get_profile(self):
        self.profile.go(slug=self.SLUG, lang=self.LANG)
        profile = self.page.get_profile()
        return profile

    @need_login
    def iter_documents(self):
        try:
            self.e_service_page.go(slug=self.SLUG)
        except ReadTimeoutError:
            raise BrowserUnavailable()

        # we might land on the documents page, but sometimes we land on user info "tab"
        self.page.select_documents_tab()
        self.page.show_more()

        # Sometimes, this page can return an error
        # Seen messages:
        # - Impossible de récupérer les relevés électroniques
        # - Le document souhaité n'a pu être généré (délai d'attente dépassé).
        #   Merci de renouveler votre demande ultérieurement.
        error = self.page.get_error_message()
        if error:
            raise BrowserUnavailable(error)

        # Sometimes two documents have the same ID (same date and same type)
        existing_id = set()
        for document in self.page.iter_documents():
            if document._url_id in existing_id:
                id_suffix = 1
                while '%s-%s' % (document._url_id, id_suffix) in existing_id:
                    id_suffix += 1
                    if id_suffix > 5:
                        # Avoid infinite loops in case of an issue
                        # There shouldn't be that many documents with the same id, we let it raise an exception
                        break
                document.id = '%s-%s' % (document._url_id, id_suffix)
            else:
                document.id = document._url_id
            existing_id.add(document.id)
            yield document


class CapeasiBrowser(S2eBrowser):
    BASEURL = 'https://www.capeasi.com'
    SLUG = 'axa'
    LANG = 'fr'  # ['fr', 'en']


class BnppereBrowser(S2eBrowser):
    BASEURL = 'https://personeo.epargne-retraite-entreprises.bnpparibas.com'
    SLUG = 'bnp'
    LANG = 'fr'  # ['fr', 'en']


class CreditdunordpeeBrowser(S2eBrowser):
    BASEURL = 'https://www.pee.credit-du-nord.fr'
    SLUG = 'cdn'
    LANG = 'fr'  # ['fr', 'en']

    pee_page = URL(r'/fr/epargnants', CreditdunordPeePage)

    def initiate_login_page(self):
        # Since Crédit du Nord and Société Générale's fusion, PEE has been moved to Esalia space.
        self.go_home()
        if self.pee_page.is_here():
            message = self.page.get_message()
            # Here is the message displayed on the page:
            # "Suite à la fusion du Groupe Crédit du Nord et Société Générale, la gestion de votre épargne salariale évolue.
            # Rendez-vous sur votre espace personnalisé du site www.esalia.com ou sur l’Appli Esalia à l'aide de vos nouveaux
            # identifiants de connexion reçus par courrier postal / mail."
            if 'Rendez-vous sur votre espace personnalisé du site www.esalia.com' in message:
                raise ActionNeeded(message)

        super().initiate_login_page()


class FederalFinanceESBrowser(S2eBrowser):
    BASEURL = 'https://www.epargne-salariale.federal-finance.fr/'
    SLUG = 'ff'
    LANG = 'fr'  # ['fr', 'en']
