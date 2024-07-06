# Copyright(C) 2023 Powens
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

from woob.browser import LoginBrowser, need_login
from woob.browser.url import BrowserParamURL, URL
from woob.capabilities.base import empty
from woob.capabilities.bank import Account
from woob.exceptions import (
    BrowserIncorrectPassword, BrowserPasswordExpired,
    ActionNeeded, ActionType, BrowserHTTPError, BrowserUnavailable,
)
from woob.tools.capabilities.bank.transactions import sorted_transactions

from .pages import (
    LoginPage, MigrationPage, AccountsPage, InvestmentPage, HistoryPage, ActionNeededPage,
    InvestDetailPage, PrevoyancePage, ValidationPage, InvestPerformancePage, MaintenancePage,
)


class AbeilleAssurancesBrowser(LoginBrowser):
    TIMEOUT = 120
    BASEURL = 'https://www.abeille-assurances.fr'

    validation = BrowserParamURL(
        r'/conventions/acceptation\?backurl=/(?P<browser_subsite>[^/]+)/Accueil',
        ValidationPage
    )
    login = BrowserParamURL(
        r'/(?P<browser_subsite>[^/]+)/MonCompte/Connexion',
        r'/(?P<browser_subsite>[^/]+)/conventions/acceptation',
        LoginPage
    )
    migration = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/MonCompte/Migration', MigrationPage)
    accounts = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/Accueil/Synthese-Contrats', AccountsPage)
    investment = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/contrat/epargne/-(?P<page_id>[0-9]{10})', InvestmentPage)
    prevoyance = BrowserParamURL(
        r'/(?P<browser_subsite>[^/]+)/contrat/prevoyance/-(?P<page_id>[0-9]{10})',
        PrevoyancePage
    )
    history = BrowserParamURL(
        r'/(?P<browser_subsite>[^/]+)/contrat/getOperations\?param1=(?P<history_token>.*)',
        HistoryPage
    )
    action_needed = BrowserParamURL(
        r'/(?P<browser_subsite>[^/]+)/coordonnees/detailspersonne\?majcontacts=true',
        r'/(?P<browser_subsite>[^/]+)/web/\?src=/tunnel',
        ActionNeededPage
    )
    invest_detail = BrowserParamURL(
        r'https://fonds-ext2.abeille-assurances.fr/sheet/fund/(?P<isin>[A-Z0-9]+)',
        InvestDetailPage
    )
    invest_performance = BrowserParamURL(
        r'https://fonds-ext2.abeille-assurances.fr/sheet/fund-calculator',
        InvestPerformancePage
    )
    maintenance = URL(r'/maintenancepage', MaintenancePage)

    def __init__(self, *args, **kwargs):
        self.subsite = 'espacepersonnel'
        super(AbeilleAssurancesBrowser, self).__init__(*args, **kwargs)

    def post_login_credentials(self):
        # Method to be overloaded by Abeille Assurances's child (Afer)
        self.page.login(self.username, self.password)

    def do_login(self):
        self.login.go()
        if self.maintenance.is_here():
            raise BrowserUnavailable()
        self.post_login_credentials()
        if self.login.is_here():
            if 'acceptation' in self.url:
                raise ActionNeeded(
                    locale="fr-FR",
                    message="Veuillez accepter les conditions générales d'utilisation sur le site.",
                    action_type=ActionType.ACKNOWLEDGE,
                )
            else:
                raise BrowserIncorrectPassword("L'identifiant ou le mot de passe est incorrect.")
        elif self.migration.is_here():
            # Usually landing here when customers have to renew their credentials
            message = self.page.get_error()
            raise BrowserPasswordExpired(message)

    @need_login
    def iter_accounts(self):
        self.accounts.go()
        for account in self.page.iter_accounts():
            # Request to account details sometimes returns a 500
            try:
                self.location(account.url)
                if not self.investment.is_here() or self.page.unavailable_details():
                    # We don't scrape insurances, guarantees, health contracts
                    # and accounts with unavailable balances
                    continue

                if not self.page.is_valuation_available():
                    # Sometimes the valuation does not appear correctly.
                    # When it happens, we try the request again; if the balance
                    # still does not appear we raise BrowserUnavailable
                    # to yield a consistent list of accounts everytime.
                    self.logger.warning('Account %s has no balance, try the request again.', account.label)
                    self.accounts.go()
                    self.location(account.url)
                    if not self.page.is_valuation_available():
                        raise BrowserUnavailable()

                self.page.fill_account(obj=account)
                if account.type == Account.TYPE_UNKNOWN:
                    self.logger.warning(
                        'Account "%s" is untyped, please check the related type in account details.',
                        account.label
                    )
                yield account
            except BrowserHTTPError:
                self.logger.warning('Could not get the account details: account %s will be skipped', account.id)

    @need_login
    def iter_investment(self, account):
        # Request to account details sometimes returns a 500
        try:
            self.location(account.url)
        except BrowserHTTPError:
            self.logger.warning('Could not get the account investments for account %s', account.id)
            return
        for inv in self.page.iter_investment():
            if not empty(inv.code):
                # Need to go first on InvestDetailPage...
                self.invest_detail.go(isin=inv.code)
                # Sometimes the page loads but there is no info
                if not self.page.is_empty():
                    # ...to then request the InvestPerformancePage tab
                    self.invest_performance.go()
                    self.page.fill_investment(obj=inv)
            yield inv

    @need_login
    def iter_history(self, account):
        if empty(account.url):
            # This account does not have a details link
            return
        try:
            self.location(account.url)
        except BrowserHTTPError:
            self.logger.warning('Could not get the history for account %s', account.id)
            return

        history_link = self.page.get_history_link()
        if not history_link:
            # Transactions are not available for this account
            return

        self.location(history_link)
        assert self.history.is_here()
        result = []
        result.extend(self.page.iter_versements())
        result.extend(self.page.iter_arbitrages())
        yield from sorted_transactions(result)
