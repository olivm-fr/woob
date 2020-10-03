# -*- coding: utf-8 -*-

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

# flake8: compatible

from __future__ import unicode_literals

import re

from weboob.browser import LoginBrowser, URL, need_login, StatesMixin
from weboob.browser.exceptions import ServerError
from weboob.exceptions import BrowserIncorrectPassword, ActionNeeded, NoAccountsException
from weboob.capabilities.wealth import Investment
from weboob.tools.capabilities.bank.investments import is_isin_valid

from .pages import (
    LoginPage, AccountsPage, AMFHSBCPage, AMFAmundiPage, AMFSGPage, HistoryPage, ErrorPage,
    LyxorfcpePage, EcofiPage, EcofiDummyPage, LandingPage, SwissLifePage, LoginErrorPage,
    EtoileGestionPage, EtoileGestionCharacteristicsPage, EtoileGestionDetailsPage,
    BNPInvestmentsPage, BNPInvestmentDetailsPage, LyxorFundsPage, EsaliaDetailsPage,
    EsaliaPerformancePage, AmundiDetailsPage, AmundiPerformancePage, ProfilePage,
    HsbcVideoPage, CprInvestmentPage, CprPerformancePage, CmCicInvestmentPage,
    HsbcInvestmentPage, EServicePage,
)


class S2eBrowser(LoginBrowser, StatesMixin):
    login = URL(
        r'/portal/salarie-(?P<slug>\w+)/authentification',
        r'(.*)portal/salarie-(?P<slug>\w+)/authentification',
        r'/portal/j_security_check', LoginPage
    )
    login_error = URL(r'/portal/login', LoginErrorPage)
    landing = URL(r'(.*)portal/salarie-bnp/accueil', LandingPage)
    accounts = URL(
        r'/portal/salarie-(?P<slug>\w+)/monepargne/mesavoirs\?language=(?P<lang>)',
        r'/portal/salarie-(?P<slug>\w+)/monepargne/mesavoirs',
        AccountsPage
    )
    history = URL(r'/portal/salarie-(?P<slug>\w+)/operations/consulteroperations', HistoryPage)
    error = URL(r'/maintenance/.+/', ErrorPage)
    profile = URL(r'/portal/salarie-(?P<slug>\w+)/mesdonnees/coordperso\?scenario=ConsulterCP', ProfilePage)
    # Amundi pages
    amfcode_amundi = URL(r'https://www.amundi-ee.com/entr/product', AMFAmundiPage)
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
    bnp_investments = URL(r'https://optimisermon.epargne-retraite-entreprises.bnpparibas.com', BNPInvestmentsPage)
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
    hsbc_video = URL(r'https://(.*)videos-pedagogiques/fonds-hsbc-ee-dynamique', HsbcVideoPage)
    amfcode_hsbc = URL(r'https://www.assetmanagement.hsbc.com/feedRequest', AMFHSBCPage)
    hsbc_investments = URL(r'https://www.assetmanagement.hsbc.com/fr/fcpe-closed', HsbcInvestmentPage)
    # CPR Asset Management pages
    cpr_investments = URL(r'https://www.cpr-am.fr/particuliers/product/view', CprInvestmentPage)
    cpr_performance = URL(r'https://www.cpr-am.fr/particuliers/ezjscore', CprPerformancePage)
    # CM-CIC investments
    cm_cic_investments = URL(
        r'https://www.cmcic-am.fr/fr/particuliers/nos-fonds/VALE_FicheSynthese.aspx',
        r'https://www.cmcic-am.fr/fr/particuliers/nos-fonds/VALE_Fiche',
        CmCicInvestmentPage
    )

    e_service_page = URL(
        r'/portal/salarie-(?P<slug>\w+)/mesdonnees/eservice\?scenario=ConsulterEService',
        r'/portal/salarie-(?P<slug>\w+)/mesdonnees/eservice',
        EServicePage,
    )

    STATE_DURATION = 10

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
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

    def do_login(self):
        otp = None
        if 'otp' in self.config:
            otp = self.config['otp'].get()

        if self.login.is_here() and otp:
            self.page.check_error()
            self.page.send_otp(otp)
            if self.login.is_here():
                self.page.check_error()
        else:
            self.login.go(slug=self.SLUG).login(self.username, self.password, self.secret)

            if self.login_error.is_here():
                raise BrowserIncorrectPassword()
            if self.login.is_here():
                error = self.page.get_error()
                if error:
                    raise ActionNeeded(error)

    @need_login
    def iter_accounts(self):
        if 'accs' not in self.cache.keys():
            no_accounts_message = None
            self.accounts.stay_or_go(slug=self.SLUG, lang=self.LANG)
            # weird wrongpass
            if not self.accounts.is_here():
                raise BrowserIncorrectPassword()
            multi_space = self.page.get_multi()
            if len(multi_space):
                # Handle multi entreprise accounts
                accs = []
                for space in multi_space:
                    space_accs = []
                    self.page.go_multi(space)
                    self.accounts.go(slug=self.SLUG)
                    if not no_accounts_message:
                        no_accounts_message = self.page.get_no_accounts_message()
                    for acc in self.page.iter_accounts():
                        acc._space = space
                        space_accs.append(acc)
                    company_name = self.profile.go(slug=self.SLUG).get_company_name()
                    for acc in space_accs:
                        acc.company_name = company_name
                    accs.extend(space_accs)
            else:
                no_accounts_message = self.page.get_no_accounts_message()
                accs = [a for a in self.page.iter_accounts()]
                company_name = self.profile.go(slug=self.SLUG).get_company_name()
                for acc in accs:
                    acc.company_name = company_name
            if not len(accs) and no_accounts_message:
                # Accounts list is empty and we found the
                # message on at least one of the spaces:
                raise NoAccountsException(no_accounts_message)
            self.cache['accs'] = accs
        return self.cache['accs']

    @need_login
    def iter_investment(self, account):
        if account.id not in self.cache['invs']:
            self.accounts.stay_or_go(slug=self.SLUG)
            # Handle multi entreprise accounts
            if hasattr(account, '_space'):
                self.page.go_multi(account._space)
                self.accounts.go(slug=self.SLUG)
            # Select account
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
                        self.location(inv._link)
                    except ServerError:
                        # For some connections, this request returns a 503 even on the website
                        self.logger.warning('Server returned a Server Error when trying to fetch investment performances.')
                        continue

                    # Access the BNP API to get the investment details using its ID (found in its label)
                    m = re.search(r'- (\d+)$', inv.label)
                    if m:
                        inv_id = m.group(1)
                        self.bnp_investment_details.go(id=inv_id)
                        self.page.fill_investment(obj=inv)
                    else:
                        self.logger.warning('Could not fetch BNP investment ID in its label, no details will be fetched.')

                elif self.amfcode_amundi.match(inv._link):
                    self.location(inv._link)
                    details_url = self.page.get_details_url()
                    performance_url = self.page.get_performance_url()
                    if details_url:
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
                    m = re.search(r'id=(\w+).+SH=(\w+)', inv._link)
                    if m:
                        params = {
                            'feed_data': 'fundbyiden',
                            'ctry': 'FR',
                            'client': 'FCPC',
                            'fId': m.group(1),
                            'lang': 'fr',
                            'SH': m.group(2),
                        }
                        self.amfcode_hsbc.go(params=params)
                        if self.amfcode_hsbc.is_here():
                            inv.code = self.page.get_code()
                            inv.code_type = Investment.CODE_TYPE_AMF
                            inv.asset_category = self.page.get_asset_category()

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
            self.accounts.stay_or_go(slug=self.SLUG)
            self.page.get_investment_pages(account.id, pocket=True)
            pockets = [p for p in self.page.iter_pocket(accid=account.id)]
            # Get page with quantity
            self.page.get_investment_pages(account.id, valuation=False, pocket=True)
            self.cache['pockets'][account.id] = self.page.update_pockets_quantity(pockets)
        return self.cache['pockets'][account.id]

    @need_login
    def iter_history(self, account):
        self.history.stay_or_go(slug=self.SLUG)
        # Handle multi entreprise accounts
        if hasattr(account, '_space'):
            self.page.go_multi(account._space)
            self.history.go(slug=self.SLUG)
        # Get more transactions on each page
        if self.page.show_more("50"):
            for tr in self.page.iter_history(accid=account.id):
                yield tr
        # Go back to first page
        self.page.go_start()

    @need_login
    def get_profile(self):
        self.profile.stay_or_go(slug=self.SLUG)
        profile = self.page.get_profile()
        return profile

    @need_login
    def iter_documents(self):
        self.e_service_page.stay_or_go(slug=self.SLUG)
        # we might land on the documents page, but sometimes we land on user info "tab"
        self.page.select_documents_tab()
        self.page.show_more()

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


class EsaliaBrowser(S2eBrowser):
    BASEURL = 'https://salaries.esalia.com'
    SLUG = 'sg'
    LANG = 'fr'  # ['fr', 'en']


class CapeasiBrowser(S2eBrowser):
    BASEURL = 'https://www.capeasi.com'
    SLUG = 'axa'
    LANG = 'fr'  # ['fr', 'en']


class ErehsbcBrowser(S2eBrowser):
    BASEURL = 'https://epargnant.ere.hsbc.fr'
    SLUG = 'hsbc'
    LANG = 'fr'  # ['fr', 'en']


class BnppereBrowser(S2eBrowser):
    BASEURL = 'https://personeo.epargne-retraite-entreprises.bnpparibas.com'
    SLUG = 'bnp'
    LANG = 'fr'  # ['fr', 'en']


class CreditdunordpeeBrowser(S2eBrowser):
    BASEURL = 'https://salaries.pee.credit-du-nord.fr'
    SLUG = 'cdn'
    LANG = 'fr'  # ['fr', 'en']
