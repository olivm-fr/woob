# Copyright(C) 2019      Budget Insight
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


from woob.browser import URL, LoginBrowser, need_login
from woob.browser.exceptions import ClientError
from woob.exceptions import BrowserIncorrectPassword, BrowserPasswordExpired

from .pages import DashboardPage, LoginPage, PasswordExpiredPage, TransactionCSV, TransactionPage


__all__ = ["BnpcartesentreprisePhenixBrowser"]


class BnpcartesentreprisePhenixBrowser(LoginBrowser):
    BASEURL = "https://corporatecards.bnpparibas.com"

    login = URL(r"/c/portal/login", r"https://connect.corporatecards.bnpparibas/login", LoginPage)
    dashboard = URL(r"/group/bddf/dashboard", DashboardPage)
    transactions_page = URL(r"/group/bddf/transactions", TransactionPage)
    transaction_csv = URL(r"/group/bddf/transactions", TransactionCSV)
    password_expired = URL(r"https://corporatecards.bnpparibas.com/group/bddf/mot-de-passe-expire", PasswordExpiredPage)

    def __init__(self, website, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.website = website
        self.corporate_browser = None

    def do_login(self):
        # these parameters are useful to get to the login area
        # if we don't use them we land in a page that has no form
        self.login.go(params={"user_type": "holder"})
        # sometimes when we switch from main to the phenix we are already in dashboard page
        # also we can be in the PasswordExpiredPage we have to change our password
        if self.login.is_here():
            try:
                self.page.login(self.username, self.password)
            except ClientError as e:
                if e.response.status_code == 401:
                    raise BrowserIncorrectPassword()
                raise

        self.dashboard.stay_or_go()

        if self.password_expired.is_here():
            raise BrowserPasswordExpired(self.page.get_error_message())

    @need_login
    def iter_accounts(self):
        self.dashboard.go()
        for account in self.page.iter_accounts():
            self.location(account.url)
            yield self.page.fill_account(obj=account)

    @need_login
    def get_transactions(self, account):
        self.dashboard.stay_or_go()
        self.location(account.url)
        self.transactions_page.go()
        params = {
            "p_p_id": "Phenix_Transactions_v2_Portlet",
            "p_p_lifecycle": "2",
            "p_p_state": "normal",
            "p_p_mode": "view",
            "p_p_resource_id": "/transactions/export",
            "p_p_cacheability": "cacheLevelPage",
        }
        instance_id = self.page.get_instance_id()
        if instance_id:
            # This part seems to be obsolete
            self.logger.warning("InstanceId url is still used")
            params.update(
                {
                    "p_p_id": "Phenix_Transactions_Portlet_INSTANCE_" + instance_id,
                    "_Phenix_Transactions_Portlet_INSTANCE_%s_MVCResourceCommand=" % instance_id: "/transaction/export",
                }
            )
            page_csv = self.transaction_csv.open(method="POST", params=params)
        else:
            data = self.page.get_form()
            page_csv = self.transaction_csv.go(data=data, params=params)

        yield from page_csv.iter_history()
