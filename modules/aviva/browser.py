# -*- coding: utf-8 -*-

# Copyright(C) 2012-2019  Budget Insight
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


from weboob.browser import LoginBrowser, need_login
from weboob.browser.url import BrowserParamURL
from weboob.capabilities.base import empty, NotAvailable
from weboob.capabilities.bank import Account
from weboob.exceptions import (
    BrowserIncorrectPassword, BrowserPasswordExpired,
    ActionNeeded, BrowserHTTPError, BrowserUnavailable,
)
from weboob.tools.capabilities.bank.transactions import sorted_transactions

from .pages.detail_pages import (
    LoginPage, MigrationPage, InvestmentPage, HistoryPage, ActionNeededPage,
    InvestDetailPage, PrevoyancePage, ValidationPage, InvestPerformancePage,
)

from .pages.account_page import AccountsPage


class AvivaBrowser(LoginBrowser):
    TIMEOUT = 120
    BASEURL = 'https://www.aviva.fr'

    validation = BrowserParamURL(r'/conventions/acceptation\?backurl=/(?P<browser_subsite>[^/]+)/Accueil', ValidationPage)
    login = BrowserParamURL(
        r'/(?P<browser_subsite>[^/]+)/MonCompte/Connexion',
        r'/(?P<browser_subsite>[^/]+)/conventions/acceptation',
        LoginPage
    )
    migration = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/MonCompte/Migration', MigrationPage)
    accounts = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/Accueil/Synthese-Contrats', AccountsPage)
    investment = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/contrat/epargne/-(?P<page_id>[0-9]{10})', InvestmentPage)
    prevoyance = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/contrat/prevoyance/-(?P<page_id>[0-9]{10})', PrevoyancePage)
    history = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/contrat/getOperations\?param1=(?P<history_token>.*)', HistoryPage)
    action_needed = BrowserParamURL(r'/(?P<browser_subsite>[^/]+)/coordonnees/detailspersonne\?majcontacts=true', ActionNeededPage)
    invest_detail = BrowserParamURL(r'https://aviva-fonds.webfg.net/sheet/fund/(?P<isin>[A-Z0-9]+)', InvestDetailPage)
    invest_performance = BrowserParamURL(r'https://aviva-fonds.webfg.net/sheet/fund-calculator', InvestPerformancePage)

    def __init__(self, *args, **kwargs):
        self.subsite = 'espaceclient'
        super(AvivaBrowser, self).__init__(*args, **kwargs)

    def do_login(self):
        self.login.go()
        self.page.login(self.username, self.password)
        if self.login.is_here():
            if 'acceptation' in self.url:
                raise ActionNeeded("Veuillez accepter les conditions générales d'utilisation sur le site.")
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
                    # to yield a consistant list of accounts everytime.
                    self.logger.warning('Account %s has no balance, try the request again.', account.label)
                    self.accounts.go()
                    self.location(account.url)
                    if not self.page.is_valuation_available():
                        raise BrowserUnavailable()

                self.page.fill_account(obj=account)
                if account.type == Account.TYPE_UNKNOWN:
                    self.logger.warning('Account "%s" is untyped, please check the related type in account details.', account.label)
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
                # ...to then request the InvestPerformancePage tab
                self.invest_performance.go()
                self.page.fill_investment(obj=inv)
            else:
                inv.unitprice = inv.diff_ratio = inv.description = NotAvailable
            yield inv

    @need_login
    def iter_history(self, account):
        if empty(account.url):
            # An account should always have a link to the details
            raise NotImplementedError()
        try:
            self.location(account.url)
        except BrowserHTTPError:
            self.logger.warning('Could not get the history for account %s', account.id)
            return

        history_link = self.page.get_history_link()

        if not history_link:
            # accounts don't always have an history_link
            raise NotImplementedError()

        self.location(history_link)
        assert self.history.is_here()
        result = []
        result.extend(self.page.iter_versements())
        result.extend(self.page.iter_arbitrages())
        return sorted_transactions(result)

    def get_subscription_list(self):
        return []
