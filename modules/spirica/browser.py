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

from requests import ConnectionError
from requests.exceptions import ProxyError

from woob.browser import URL, LoginBrowser, need_login
from woob.browser.exceptions import LoggedOut, ServerError
from woob.browser.retry import retry_on_logout
from woob.capabilities.bank import Account
from woob.capabilities.bank.wealth import Per, PerVersion
from woob.capabilities.base import NotAvailable
from woob.exceptions import BrowserIncorrectPassword, BrowserUnavailable

from .pages import AccountsPage, DetailsPage, LoginPage, MaintenancePage


class SpiricaBrowser(LoginBrowser):
    TIMEOUT = 180

    login = URL("/securite/login.xhtml", LoginPage)
    accounts = URL("/sylvea/client/synthese.xhtml", AccountsPage)
    details = URL("/sylvea/contrat/consultationContratEpargne.xhtml", DetailsPage)
    maintenance = URL("/maintenance.html", MaintenancePage)

    def __init__(self, website, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.BASEURL = website
        self.cache = {}
        self.cache["invs"] = {}
        self.transaction_page = None
        self.accounts_ids_list = []

    def do_login(self):
        try:
            self.login.go()
        except ConnectionError as e:
            # The ConnectionError is raised when the call is blocked.
            if isinstance(e, ProxyError):
                # ProxyError inherits ConnectionError but should be raised as is.
                raise e
            raise BrowserUnavailable(e)
        self.page.login(self.username, self.password)

        if self.login.is_here():
            error = self.page.get_error()
            raise BrowserIncorrectPassword(error)

    def get_subscription_list(self):
        return iter([])

    @retry_on_logout()
    @need_login
    def go_to_details_page(self, account):
        self.location(account.url)
        if self.login.is_here():
            raise LoggedOut()

    def is_new_account(self):
        # in this website we make a request and get the first account
        # then another request to get the second, then then ...
        # after iterating over all accounts, the website returns again the first account
        self.accounts.go()
        seen_accounts_ids = list(self.page.get_account_id())
        switch_account_form = self.page.get_switch_account_form()

        switch_account_form.submit()
        account_id = self.page.get_account_id()
        while account_id not in seen_accounts_ids:
            seen_accounts_ids.append(account_id)
            if account_id not in self.accounts_ids_list:
                return True
            switch_account_form.submit()
            account_id = self.page.get_account_id()

        return False

    def get_account(self):
        account = self.page.get_account()
        self.go_to_details_page(account)
        data = self.page.get_account_data()
        account.valuation_diff = data["valuation_diff"]
        account._raw_label = data["_raw_label"]
        account.type = data["type"]

        if account.type == Account.TYPE_PER:
            per = Per.from_dict(account.to_dict())
            if account._raw_label == "PERIN":
                per.version = PerVersion.PERIN
            else:
                self.logger.warning("Unhandled PER version: %s", account._raw_label)
                per.version = NotAvailable
            return per
        else:
            return account

    @need_login
    def iter_accounts(self):
        self.accounts.go()
        if self.page.has_multiple_accounts():
            # We have no means whatsoever to know how many accounts there are on the user space.
            # On the website, you can only, click on "Sélectionner un autre contrat",
            # this allows us to switch to another contract on the website.
            while self.accounts_ids_list == [] or self.is_new_account():
                account = self.get_account()
                self.accounts_ids_list.append(account.id)
                yield account
        else:
            yield self.get_account()

    @need_login
    def iter_investment(self, account):
        if account.id not in self.cache["invs"]:
            # Get form to show PRM
            self.location(account.url)
            self.page.goto_unitprice()
            invs = [i for i in self.page.iter_investment()]
            invs_pm = [i for i in self.page.iter_pm_investment()]
            self.fill_from_list(invs, invs_pm)
            self.cache["invs"][account.id] = invs
        return self.cache["invs"][account.id]

    def check_if_logged_in(self, url):
        if self.login.is_here():
            self.logger.warning("We were logged out during iter_history, proceed to re-login.")
            self.do_login()
            self.location(url)
            self.page.go_historytab()
            # Store new transaction_page after login:
            self.transaction_page = self.page

    @need_login
    def iter_history(self, account):
        try:
            self.location(account.url)
        except ServerError:
            # We have to handle 'fake' 500 errors, which are probably due to Spirica blocking the IPs
            # Quite often, these errors cause logouts so we may have to re-login.
            self.logger.warning("Access to account details has failed due to a 500 error. We try again.")
            if self.login.is_here():
                self.logger.warning("Server error led to a logout, we must re-login.")
                self.do_login()
            self.accounts.go()
            try:
                self.location(account.url)
            except ServerError:
                error_message = "Access to details for accounts %s has failed twice." % account.id
                self.logger.warning(error_message)
                raise BrowserUnavailable(error_message)

        self.page.go_historytab()
        self.transaction_page = self.page

        # Determining the number of transaction pages:
        total_pages = int(self.page.count_transactions()) // 100
        for page_number in range(total_pages + 1):
            self.check_if_logged_in(account.url)
            if not self.transaction_page.go_historyall(page_number):
                self.logger.warning("The first go_historyall() failed, go back to account details and retry.")
                self.location(account.url)
                self.page.go_historytab()
                self.transaction_page = self.page
                if not self.transaction_page.go_historyall(page_number):
                    self.logger.warning("The go_historyall() failed twice, these transactions will be skipped.")
                    continue
            yield from self.page.iter_history()

    def fill_from_list(self, invs, objects_list):
        matching_fields = ["code", "unitvalue", "label", "_gestion_type"]
        for inv in invs:
            # Some investments don't have PRM
            if inv._invest_type != "Fonds en euros":
                inv_fields = {field: getattr(inv, field, None) for field in matching_fields}
                obj_from_list = []
                for o in objects_list:
                    if all(getattr(o, field) == inv_fields.get(field) for field in matching_fields):
                        obj_from_list.append(o)
                assert len(obj_from_list) == 1
                for name, field_value in obj_from_list[0].iter_fields():
                    if field_value:
                        setattr(inv, name, field_value)
