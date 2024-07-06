# -*- coding: utf-8 -*-

# Copyright(C) 2012-2013  Romain Bignon
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
from collections import OrderedDict
from datetime import timedelta, date
from urllib.parse import parse_qsl, urlparse

from dateutil.relativedelta import relativedelta
from lxml.etree import XMLSyntaxError

from woob.tools.date import LinearDateGuesser
from woob.capabilities.bank import Account, AccountNotFound, AccountOwnership
from woob.capabilities.bank.base import Loan
from woob.tools.capabilities.bank.transactions import sorted_transactions, keep_only_card_transactions
from woob.tools.value import Value
from woob.exceptions import (
    BrowserIncorrectPassword, BrowserPasswordExpired, BrowserUnavailable,
    BrowserUserBanned, BrowserQuestion,
)
from woob.browser import URL, need_login
from woob.browser.mfa import TwoFactorBrowser
from woob.browser.exceptions import HTTPNotFound
from woob.capabilities.base import find_object

from .pages.account_pages import (
    AccountsPage, AppGonePage, CBOperationPage, CPTOperationPage, FrameContainer, LoanDetailsPage, LoginPage,
    OtherPage, OwnersListPage, ProfilePage, RibPage, ScpiHisPage, UnavailablePage,
    AppGoneException,
)
from .pages.document_pages import DocumentPage
from .pages.life_insurances import (
    LifeInsurancesPage, LifeInsurancePortal, LifeInsuranceMain, LifeInsuranceUseless,
    LifeNotFound, LifeInsuranceFingerprintForm,
)
from .pages.investments import (
    LogonInvestmentPage, ProductViewHelper, RetrieveAccountsPage, RetrieveInvestmentsPage,
    RetrieveLiquidityPage, RetrieveUselessPage, ScpiInvestmentPage,
)
from .pages.landing_pages import JSMiddleFramePage, JSMiddleAuthPage, InvestmentFormPage


__all__ = ['HSBC']


class HSBC(TwoFactorBrowser):
    BASEURL = 'https://clients.hsbc.fr'
    TIMEOUT = 30
    HAS_CREDENTIALS_ONLY = True

    app_gone = False

    scpi_investment_page = URL(r'https://www.hsbc.fr/1/[0-9]/.*', ScpiInvestmentPage)
    scpi_his_page = URL(r'https://www.hsbc.fr/1/[0-9]/.*', ScpiHisPage)

    connection = URL(r'https://www.hsbc.fr/1/2/hsbc-france/particuliers/connexion', LoginPage)
    connection2 = URL(r'https://www.hsbc.fr/1/2//hsbc-france/particuliers/connexion', LoginPage)
    login = URL(r'https://www.hsbc.fr/1/*', LoginPage)
    cptPage = URL(
        r'/cgi-bin/emcgi.*\&Cpt=.*',
        r'/cgi-bin/emcgi.*\&Epa=.*',
        r'/cgi-bin/emcgi.*\&CPT_IdPrestation.*',
        r'/cgi-bin/emcgi.*\&Ass_IdPrestation.*',
        # FIXME are the previous patterns relevant in POST nav?
        r'/cgi-bin/emcgi',
        CPTOperationPage,
    )
    cbPage = URL(
        r'/cgi-bin/emcgi.*[\&\?]Cb=.*',
        r'/cgi-bin/emcgi.*\&CB_IdPrestation.*',
        # FIXME are the previous patterns relevant in POST nav?
        r'/cgi-bin/emcgi',
        CBOperationPage,
    )
    appGone = URL(
        r'/.*_absente.html',
        r'/pm_absent_inter.html',
        r'/appli_absente_MBEL.html',
        r'/pm_absent_inter_MBEL.html',
        AppGonePage,
    )
    rib = URL(r'/cgi-bin/emcgi', RibPage)
    accounts = URL(r'/cgi-bin/emcgi', AccountsPage)
    owners_list = URL(r'/cgi-bin/emcgi', OwnersListPage)
    life_insurance_useless = URL(r'/cgi-bin/emcgi', LifeInsuranceUseless)

    profile = URL(r'/cgi-bin/emcgi', ProfilePage)
    unavailable = URL(r'/cgi-bin/emcgi', UnavailablePage)
    frame_page = URL(
        r'/cgi-bin/emcgi',
        r'https://clients.hsbc.fr/cgi-bin/emcgi',
        FrameContainer,
    )

    # other site
    life_insurance_portal = URL(r'/cgi-bin/emcgi', LifeInsurancePortal)
    life_insurance_main = URL(
        r'https://assurances.hsbc.fr/fr/accueil/b2c/accueil.html\?pointEntree=PARTIEGENERIQUEB2C',
        LifeInsuranceMain
    )
    life_insurances = URL(r'https://assurances.hsbc.fr/navigation', LifeInsurancesPage)
    life_not_found = URL(r'https://assurances.hsbc.fr/fr/404.html', LifeNotFound)
    life_insurance_fingerprint_form = URL(r'/cgi-bin/emcgi', LifeInsuranceFingerprintForm)

    # investment pages
    middle_frame_page = URL(r'/cgi-bin/emcgi', JSMiddleFramePage)
    middle_auth_page = URL(r'/cgi-bin/emcgi', JSMiddleAuthPage)
    investment_form_page = URL(
        r'https://www.hsbc.fr/1/[0-9]/authentication/sso-cwd\?customerFullName=.*',
        InvestmentFormPage
    )
    logon_investment_page = URL(
        r'https://investissements.clients.hsbc.fr/group-wd-gateway-war/gateway/LogonAuthentication',
        r'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/LogonAuthentication',
        LogonInvestmentPage
    )
    retrieve_accounts_view = URL(
        r'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/wd/RetrieveCustomerPortfolio',
        RetrieveAccountsPage
    )
    retrieve_investments_page = URL(
        r'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/wd/RetrieveCustomerPortfolio',
        RetrieveInvestmentsPage
    )
    retrieve_liquidity_page = URL(
        r'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/wd/RetrieveCustomerPortfolio',
        RetrieveLiquidityPage
    )
    retrieve_useless_page = URL(
        r'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/wd/RetrieveCustomerPortfolio',
        RetrieveUselessPage
    )

    # loan details page
    loan_details = URL(r'/cgi-bin/emcgi\?.*&CRE_CdBanque=.*&CRE_IdPrestation=.*', LoanDetailsPage)

    documents = URL(r'/cgi-bin/emcgi', DocumentPage)

    # catch-all
    other_page = URL(r'/cgi-bin/emcgi', OtherPage)

    def __init__(self, config, username, password, secret, *args, **kwargs):
        self.config = config
        super(HSBC, self).__init__(config, username, password, *args, **kwargs)
        self.accounts_dict = OrderedDict()
        self.unique_accounts_dict = dict()
        self.secret = secret
        self.PEA_LISTING = {}
        self.owners_url_list = []
        self.web_space = None
        self.home_url = None
        self.AUTHENTICATION_METHODS = {
            'otp': self.handle_otp,
        }
        self.otp_form_data = None
        self.otp_validation_url = None
        self.__states__ += ('otp_form_data', 'otp_validation_url',)

    def load_state(self, state):
        # when the otp is being handled, we want to keep the same session
        if self.config['otp'].get():
            state.pop('url', None)
            super(HSBC, self).load_state(state)

    def handle_otp(self):
        otp = self.config['otp'].get()

        # In some scenarios relogin will be triggered (see AppGonePage).
        # We need to set config['otp'] to None, otherwise we will try to validate
        # the otp once again even though we might not be on the right page anymore.
        self.config['otp'].set(self.config['otp'].default)

        if not self.otp_form_data or not self.otp_validation_url:
            # An ActionNeeded can happen during handle_otp(),
            # but self.otp_form_data and self.otp_form_url would have been
            # set to None and the OTP would already been submitted and accepted by the server.
            #
            # To avoid running handle_otp a second time, we check
            # if self.otp_form_data and self.otp_validation_url are present.
            # If they're not, we call init_login() where the SCA won't be triggered.
            self.logger.info(
                "We have an OTP but we don't have the OTP form and/or the OTP validation url."
                + " Restarting the login process..."
            )
            return self.init_login()

        self.otp_form_data['memorableAnswer'] = self.secret
        self.otp_form_data['idv_OtpCredential'] = otp

        try:
            self.location(self.otp_validation_url, data=self.otp_form_data)  # validate the otp

            # This is to make sure that we won't run handle_otp() a second time
            # if an ActionNeeded occurs during handle_otp().
            self.otp_form_data = self.otp_form_url = None
            self.end_login()
        except AppGoneException:
            self.app_gone = True
            self.logger.info('Application has gone. Relogging...')
            self.do_logout()
            self.do_login()

    def check_login_error(self):
        error_msg = self.page.get_error()

        if error_msg:
            if 'Please click Reset Credentials' in error_msg or 'Please reset your HSBC Secure Key' in error_msg:
                raise BrowserPasswordExpired(error_msg)

            elif 'Please retry in 30 minutes' in error_msg:
                raise BrowserUserBanned(error_msg)

            elif 'The service is temporarily unavailable' in error_msg:
                raise BrowserUnavailable(error_msg)

            raise AssertionError('Unhandled error at login: %s' % error_msg)

    def get_otp_validation_url(self, otp_url):
        # This method is useful for children modules that don't share the same validation url for otp
        # The url is hardcoded here, because the baseurl changed during the otp_validation request
        if 'https://' in otp_url:
            return otp_url
        return 'https://www.hsbc.fr' + otp_url

    def init_login(self):
        self.session.cookies.clear()

        self.app_gone = False
        # The website seems to be using the connection2 URL now for login, it seems weird
        # that there is randomly 2 `/` in the URL so i let the try on the first connection
        # in case they revert the change.
        try:
            self.connection.go()
        except HTTPNotFound:
            self.connection2.go()

        self.page.login(self.username)
        # The handling of 2FA is unusual. When authenticating, the user has the choice to use an OTP or his password
        # when the sca is required, the link to log on the website without otp is not available. That's how we know
        # this is the only available authentication method.
        no_secure_key_link = self.page.get_no_secure_key_link()

        # to test the sca, just invert the following if condition, authentication using an otp is always available
        if no_secure_key_link:
            self.location(no_secure_key_link)
        else:
            self.check_login_error()
            self.check_interactive()

            otp_form = self.page.get_form(nr=0)
            self.otp_form_data = dict(otp_form)
            self.otp_validation_url = self.get_otp_validation_url(otp_form.url)
            raise BrowserQuestion(
                Value(
                    'otp',
                    label='''Veuillez entrer un code à usage unique à générer depuis votre application HSBC (bouton "Générer un code à usage unique" sur la page de login de l'application)''',
                )
            )
        self.page.login_w_secure(self.password, self.secret)
        self.end_login()

    def end_login(self):
        for _ in range(3):
            if self.login.is_here():
                if not self.page.logged:
                    # we should be logged in at this point
                    self.check_login_error()
                self.page.useless_form()

        if self.frame_page.is_here():
            self.home_url = self.page.get_frame()
            self.js_url = self.page.get_js_url()

        if not self.home_url or not self.page.logged:
            raise BrowserIncorrectPassword()

        self.location(self.home_url)

    def go_post(self, url, data=None):
        # most of HSBC accounts links are actually handled by js code
        # which convert a GET query string to POST data.
        # not doing so often results in logout by the site
        q = dict(parse_qsl(urlparse(url).query))
        if data:
            q.update(data)
        url = url[:url.find('?')]
        self.location(url, data=q)

    def go_to_owner_accounts(self, owner):
        """
        The owners URLs change all the time so we must refresh them.
        If we try to go to a person's accounts page while we are already
        on this page, the website returns an empty page with the message
        "Pas de TIERS", so we must always go to the owners list before
        going to the owner's account page.
        """

        if not self.owners_list.is_here():
            self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})

            if not self.owners_list.is_here():
                # Sometimes when we fetch info from a PEA account, the first POST
                # fails and we are blocked on some owner's AccountsPage.
                self.logger.warning('The owners list redirection failed, we must try again.')
                self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})

        # Refresh owners URLs in case they changed:
        self.owners_url_list = self.page.get_owners_urls()
        self.go_post(self.owners_url_list[owner])

    @need_login
    def iter_account_owners(self):
        """
        Some connections have a "Compte de Tiers" section with several
        people each having their own accounts. We must fetch the account
        for each person and store the owner of each account.
        """
        if not self.web_space:
            if not self.accounts.is_here():
                self.go_post(self.js_url, data={'debr': 'COMPTES_PAN'})
            # get_web_space will set the value of self.web_space
            self.page.get_web_space()

        if not self.unique_accounts_dict and self.web_space == 'new_space':
            # Go to the owners list to find the list of other owners
            self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})
            self.owners_url_list = self.page.get_owners_urls()

            for owner in range(len(self.owners_url_list)):
                self.accounts_dict[owner] = {}
                self.update_accounts_dict(owner)

                # We must set an "_owner" attribute to each account.
                for a in self.accounts_dict[owner].values():
                    a._owner = owner

                    # The first space is the PSU owner space
                    if owner == 0:
                        a.ownership = AccountOwnership.OWNER
                    else:
                        a.ownership = AccountOwnership.ATTORNEY

                # go on cards page if there are cards accounts
                for a in self.accounts_dict[owner].values():
                    if a.type == Account.TYPE_CARD:
                        self.location(a.url)
                        break

                # get all couples (card, parent) on card page
                all_card_and_parent = []
                if self.cbPage.is_here():
                    all_card_and_parent = self.page.get_all_parent_id()
                    self.go_post(self.js_url, data={'debr': 'COMPTES_PAN'})

                # update cards parent and currency
                for a in self.accounts_dict[owner].values():
                    if a.type == Account.TYPE_CARD:
                        for card in all_card_and_parent:
                            # card[0] and card[1] are labels containing the id for the card and its parents account, respectively
                            # cut spaces in labels such as 'CARTE PREMIER N° 1234 00XX XXXX 5678'
                            if a.id in card[0].replace(' ', ''):
                                # ids in the HTML have 5 numbers added at the beginning, catch only the end
                                parent_id = re.match(r'^(\d*)?(\d{11}EUR)$', card[1]).group(2)
                                a.parent = find_object(self.accounts_dict[owner].values(), id=parent_id)

                            if a.parent and not a.currency:
                                a.currency = a.parent.currency

                # get loans infos
                for account_id, account in self.accounts_dict[owner].items():
                    if account.type == Account.TYPE_LOAN:
                        account = Loan.from_dict(account.to_dict())
                        # we must set owner to Loans
                        account._owner = owner
                        self.fill_loan(account)
                        self.accounts_dict[owner][account_id] = account

                # We must get back to the owners list before moving to the next owner:
                self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})

            # Fill a dictionary will all accounts without duplicating common accounts:
            for owner in self.accounts_dict.values():
                for account in owner.values():
                    if account.id not in self.unique_accounts_dict.keys():
                        self.unique_accounts_dict[account.id] = account
                    else:
                        # If an account is in multiple space, that's mean it is shared between this owners.
                        self.unique_accounts_dict[account.id].ownership = AccountOwnership.CO_OWNER

        if self.unique_accounts_dict:
            for account in self.unique_accounts_dict.values():
                if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION, Account.TYPE_PERP):
                    self.update_life_insurance_balance(account)
                yield account
        else:
            # TODO ckeck GrayLog and get rid of old space code if clients are no longer using it
            self.logger.warning('Passed through the old HSBC webspace')
            self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})
            self.owners_url_list = self.page.get_owners_urls()

            # self.accounts_dict will be a dictionary of owners each
            # containing a dictionary of the owner's accounts.
            for owner in range(len(self.owners_url_list)):
                self.accounts_dict[owner] = {}
                self.update_accounts_dict(owner)

                # We must set an "_owner" attribute to each account.
                for a in self.accounts_dict[owner].values():
                    a._owner = owner

                # go on cards page if there are cards accounts
                for a in self.accounts_dict[owner].values():
                    if a.type == Account.TYPE_CARD:
                        self.location(a.url)
                        break

                # get all couples (card, parent) on cards page
                all_card_and_parent = []
                if self.cbPage.is_here():
                    all_card_and_parent = self.page.get_all_parent_id()
                    self.go_post(self.js_url, data={'debr': 'COMPTES_PAN'})

                # update cards parent and currency
                for a in self.accounts_dict[owner].values():
                    if a.type == Account.TYPE_CARD:
                        for card in all_card_and_parent:
                            if a.id in card[0].replace(' ', ''):
                                a.parent = find_object(self.accounts_dict[owner].values(), id=card[1])
                            if a.parent and not a.currency:
                                a.currency = a.parent.currency

                # We must get back to the owners list before moving to the next owner:
                self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})

            # Fill a dictionary will all accounts without duplicating common accounts:
            for owner in self.accounts_dict.values():
                for account in owner.values():
                    if account.id not in self.unique_accounts_dict.keys():
                        self.unique_accounts_dict[account.id] = account
            for account in self.unique_accounts_dict.values():
                if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION, Account.TYPE_PERP):
                    self.update_life_insurance_balance(account)
                yield account

    def fill_loan(self, loan):
        if loan.url:
            self.location(loan.url)
            self.page.fill_loan(obj=loan)

    # To get most updated balance we need to go to account's LifeInsurancesPage
    # as main dashboard does not provide daily updates
    def update_life_insurance_balance(self, account):
        try:
            if not self._go_to_life_insurance(account):
                self._quit_li_space()
                return account
        except (XMLSyntaxError, HTTPNotFound):
            self._quit_li_space()
            return account
        except AccountNotFound:
            return account

        self.page.update_balance(obj=account)

        self._quit_li_space()

        return account

    @need_login
    def update_accounts_dict(self, owner, iban=True):
        # Go to the owner's account page in case we are not there already:
        self.go_to_owner_accounts(owner)

        for a in self.page.iter_spaces_account():
            try:
                self.accounts_dict[owner][a.id].url = a.url
            except KeyError:
                self.accounts_dict[owner][a.id] = a

        if iban:
            self.location(self.js_url, params={'debr': 'COMPTES_RIB'})
            if self.rib.is_here():
                self.page.get_rib(self.accounts_dict[owner])

    @need_login
    def _quit_li_space(self):
        if self.life_insurances.is_here():
            self.page.disconnect()

            self.session.cookies.pop('ErisaSession', None)
            self.session.cookies.pop('HBFR-INSURANCE-COOKIE-82', None)

        if self.life_not_found.is_here():
            # likely won't avoid having to login again anyway
            self.location(self.js_url)

        if self.frame_page.is_here():
            home_url = self.page.get_frame()
            self.js_url = self.page.get_js_url()

            self.location(home_url)

        if self.life_insurance_useless.is_here():
            data = {'debr': 'COMPTES_PAN'}
            self.go_post(self.js_url, data=data)

    @need_login
    def _go_to_life_insurance(self, account):
        self._quit_li_space()

        # We need to be on the account's owner space if we want to access the life insurances website.
        self.go_post(self.js_url, data={'debr': 'SORTIE_ACCES_TIERS'})
        self.go_to_owner_accounts(account._owner)

        self.go_post(account.url)

        if (
                self.accounts.is_here()
                or self.frame_page.is_here()
                or self.life_insurance_useless.is_here()
                or self.life_not_found.is_here()
        ):
            self.logger.warning('cannot go to life insurance %r', account)
            return False

        if self.life_insurance_fingerprint_form.is_here():
            self.logger.warning('cannot go to life insurance %r because of a fingerprinting form', account)
            return False

        assert self.life_insurances.is_here(), 'Not on the expected LifeInsurancesPage'

        self.page.post_li_form(account.id)
        return True

    @need_login
    def get_history(self, account, coming=False, retry_li=True):
        self._quit_li_space()
        #  Update accounts list only in case of several owners
        if len(self.owners_url_list) > 1:
            self.update_accounts_dict(account._owner, iban=False)
        account = self.accounts_dict[account._owner][account.id]

        if account.url is None:
            return []

        if account.url.startswith('javascript') or '&Crd=' in account.url or account.type == Account.TYPE_LOAN:
            raise NotImplementedError()

        if account.type == Account.TYPE_MARKET and 'BOURSE_INV' not in account.url:
            # Clean account url
            m = re.search(r"'(.*)'", account.url)
            if m:
                account_url = m.group(1)
            else:
                account_url = account.url
            # Need to be on owner's accounts page to go on scpi page
            self.go_to_owner_accounts(account._owner)
            # Go on scpi page
            self.location(account_url)
            self.location(self.page.go_scpi_his_detail_page())

            return self.page.iter_history()

        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION, Account.TYPE_PERP):
            if coming is True:
                return []

            try:
                if not self._go_to_life_insurance(account):
                    self._quit_li_space()
                    return []
            except (XMLSyntaxError, HTTPNotFound):
                self._quit_li_space()
                return []
            except AccountNotFound:
                self.go_post(self.js_url)

                # often if we visit life insurance subsite multiple times too quickly, the site just returns an error
                # so we just retry (we might relogin...)
                # TODO find out how to avoid the error, or avoid relogin
                if retry_li:
                    self.logger.warning('life insurance seems unavailable for account %s', account.id)
                    return self.get_history(account, coming, False)

                self.logger.error('life insurance seems unavailable for account %s', account.id)
                return []

            self.page.post_li_history_form()

            history = [t for t in self.page.iter_history()]

            self._quit_li_space()

            return history

        try:
            self.go_post(account.url)
        # sometime go to hsbc life insurance space do logout
        except HTTPNotFound:
            self.app_gone = True
            self.do_logout()
            self.do_login()
        # If we relogin on hsbc, all links have changed
        if self.app_gone:
            self.app_gone = False
            self.update_accounts_dict(account._owner, iban=False)
            self.location(self.accounts_dict[account._owner][account.id].url)

        if self.page is None:
            return []

        # for 'fusion' and 'new' space there is a form to submit on the page to go the account's history
        if hasattr(account, '_is_form') and account._is_form:
            # go on accounts page to get account form
            self.go_to_owner_accounts(account._owner)
            self.go_post(self.js_url, data={'debr': 'COMPTES_PAN'})
            self.page.go_history_page(account)

        if self.cbPage.is_here():
            history_tabs_urls = self.page.history_tabs_urls()
            guesser = LinearDateGuesser(date_max_bump=timedelta(45))
            history = []
            if len(history_tabs_urls) == 0:
                # no operation within the supported historical period
                return history
            # gather coming anyway
            # in case no new transaction has been recorded since last (past) payement
            self.location(history_tabs_urls[0])  # fetch only first tab coming transactions
            history.extend(list(self.page.get_history(date_guesser=guesser)))
            if not coming:
                # get further history
                self.logger.debug("get history")
                for tab in history_tabs_urls[1:]:
                    self.location(tab)  # fetch all tab but first of past transactions
                    history += list(self.page.get_history(date_guesser=guesser))

            for tr in history:
                if tr.type == tr.TYPE_UNKNOWN:
                    tr.type = tr.TYPE_DEFERRED_CARD
                    tr.bdate = tr.rdate

            if account.parent:
                # Fetching the card summaries from the parent account using the card id in the transaction labels:
                def match_card(tr):
                    return (account.id in tr.label.replace(' ', ''))
                history.extend(keep_only_card_transactions(self.get_history(account.parent), match_card))

            history = [
                tr
                for tr in history
                if (
                    (coming and tr.date > date.today())
                    or (not coming and tr.date <= date.today())
                )
            ]
            history = sorted_transactions(history)
            return history
        elif self.life_insurance_useless.is_here():
            return []
        elif not coming:
            return self._get_history()
        else:
            raise NotImplementedError()

    def _get_history(self):
        for tr in self.page.get_history():
            yield tr

    def get_investments(self, account, retry_li=True):
        if not account.url:
            raise NotImplementedError()
        if account.type in (
            Account.TYPE_LIFE_INSURANCE,
            Account.TYPE_CAPITALISATION,
            Account.TYPE_PERP,
        ):
            return self.get_life_investments(account, retry_li=retry_li)
        elif account.type == Account.TYPE_PEA:
            return self.get_pea_investments(account)
        elif account.type == Account.TYPE_MARKET:
            # 'BOURSE_INV' need more security to get invest page
            if 'BOURSE_INV' in account.url:
                return self.get_pea_investments(account)
            return self.get_scpi_investments(account)
        else:
            raise NotImplementedError()

    def get_scpi_investments(self, account):
        if not account.url:
            raise NotImplementedError()
        # Clean account url
        m = re.search(r"'(.*)'", account.url)
        if m:
            account_url = m.group(1)
        else:
            account_url = account.url

        # Need to be on accounts page to go on scpi page
        try:
            self.go_to_owner_accounts(account._owner)
            self.accounts.go()
        except AppGoneException:
            pass

        # Go on scpi page
        self.location(account_url)
        # Go on scpi details page
        self.page.go_scpi_detail_page()
        # If there is more details page, go on that page
        self.page.go_more_scpi_detail_page()
        return self.page.iter_scpi_investment()

    def get_pea_investments(self, account):
        # We need to be on the account's owner space if we want to access the investments website.
        self.go_post(self.js_url, data={'debr': 'SORTIE_ACCES_TIERS'})
        self.go_to_owner_accounts(account._owner)
        assert account.type in (Account.TYPE_PEA, Account.TYPE_MARKET)

        # When invest balance is 0, there is not link to go on market page
        # Or if we try to fetch "Compte de Tiers" the website return :
        # "Cette prestation n'est pas accessible en mode accounts tiers."
        if not account.balance or account._owner != 0:
            return []

        if not self.PEA_LISTING:
            # _go_to_wealth_accounts returns True if everything went well.
            if not self._go_to_wealth_accounts(account):
                self.logger.warning('Unable to connect to wealth accounts.')
                return []

        # Get account number without "EUR"
        account_id = re.search(r'\d{4,}', account.id).group(0)
        pea_invests = []
        account = None

        if 'accounts' in self.PEA_LISTING:
            for acc in self.PEA_LISTING['accounts']:
                # acc.id is like XXX<account number>
                if account_id in acc.id:
                    account = acc
                    break
        # Account should be found
        assert account

        if 'liquidities' in self.PEA_LISTING:
            for liquidity in self.PEA_LISTING['liquidities']:
                if liquidity._invest_account_id == account.number:
                    pea_invests.append(liquidity)
        if 'investments' in self.PEA_LISTING:
            for invest in self.PEA_LISTING['investments']:
                if invest._invest_account_id == account.id:
                    pea_invests.append(invest)
        return pea_invests

    def get_life_investments(self, account, retry_li=True):
        self._quit_li_space()
        self.update_accounts_dict(account._owner, False)
        account = self.accounts_dict[account._owner][account.id]
        try:
            if not self._go_to_life_insurance(account):
                self._quit_li_space()
                return []
        except (XMLSyntaxError, HTTPNotFound):
            self._quit_li_space()
            return []
        except AccountNotFound:
            self.go_post(self.js_url)

            # often if we visit life insurance subsite multiple times too quickly, the site just returns an error
            # retry (we might relogin...)
            if retry_li:
                self.logger.warning('life insurance seems unavailable for account %s', account.id)
                return self.get_investments(account, False)

            self.logger.error('life insurance seems unavailable for account %s', account.id)
            return []

        investments = [i for i in self.page.iter_investments()]

        self._quit_li_space()

        return investments

    def _go_to_wealth_accounts(self, account):
        if not hasattr(self.page, 'get_middle_frame_url'):
            # if we can catch the URL, we go directly, else we need to browse
            # the website
            self.update_accounts_dict(account._owner, False)

        self.location(self.page.get_middle_frame_url())

        if self.page.get_patrimoine_url():
            self.location(self.page.get_patrimoine_url())
            # Sometime we cannot access the investments pages
            if not self.middle_auth_page.is_here():
                return False
            try:
                self.page.go_next()
            except BrowserUnavailable:
                # Some wealth accounts are on linebourse and can't be accessed with the current authentication.
                return False

            if self.login.is_here():
                self.logger.warning('Connection to the Logon page failed, we must try again.')
                self.do_login()
                self.update_accounts_dict(account._owner, False)
                self.investment_form_page.go()
                # If reloggin did not help accessing the wealth space,
                # there is nothing more we can do to get there.
                if not self.investment_form_page.is_here():
                    return False

            try:
                self.page.go_to_logon()
            except HTTPNotFound:
                # Sometimes the submitted form redirects to a 404 error page
                return False
            helper = ProductViewHelper(self)
            # we need to go there to initialize the session
            self.PEA_LISTING['accounts'] = list(helper.retrieve_accounts())
            self.PEA_LISTING['liquidities'] = list(helper.retrieve_liquidity())
            self.PEA_LISTING['investments'] = list(helper.retrieve_invests())
            self.connection.go()
            return True

    @need_login
    def get_profile(self):
        if not self.owners_url_list:
            self.go_post(self.js_url, data={'debr': 'OPTIONS_TIE'})
            if self.owners_list.is_here():
                self.owners_url_list = self.page.get_owners_urls()

        # The main owner of the connection is always the first of the list:
        self.go_to_owner_accounts(0)
        data = {'debr': 'PARAM'}
        self.go_post(self.js_url, data=data)
        return self.page.get_profile()

    @need_login
    def iter_subscriptions(self):
        self.go_post(self.js_url, data={'debr': 'E_RELEVES_BP'})
        return self.page.iter_subscriptions()

    @need_login
    def iter_documents(self, subscription):
        self.go_post(self.js_url, data={'debr': 'E_RELEVES_BP'})
        today = date.today()
        start_date = today - relativedelta(years=1)

        self.page.go_to_documents(subscription._idx_account, start_date)
        return self.page.iter_documents(subid=subscription.id, idx_account=subscription._idx_account)
