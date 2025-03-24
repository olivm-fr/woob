# Copyright(C) 2024      Ludovic LANGE
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

import random
from datetime import date

from dateutil.relativedelta import relativedelta
from base64 import b64encode
from hashlib import sha256

from woob.browser import URL, need_login
from woob.browser.browsers import ClientError, ServerError
from woob.capabilities.bank import Account
from woob.tools.decorators import retry
from woob_modules.cmso.par.browser import CmsoParBrowser

from .pages import AccountsPage, BalancePage, DocumentsPage, RibPage, SubscriptionsPage, TransactionsPage


__all__ = ["CCFParBrowser", "CCFProBrowser"]


class CCFBrowser(CmsoParBrowser):
    arkea = "MG"  # Needed for the X-ARKEA-EFS header
    arkea_si = None
    AUTH_CLIENT_ID = "S4dgkKwTA7FQzWxGRHPXe6xNvihEATOY"

    # Use CmsoParBrowser as base, but rely on /distri-account-api/api
    # for accounts list & balance. Like modules/allianzbanque/browser.py
    # We should probably extract a common browser.

    balances_comings = URL(
        r"/distri-account-api/api/v1/persons/me/accounts/(?P<account_id>[A-Z0-9]{10})/total-upcoming-transactions",
        AccountsPage,
    )
    # accounts_: note the trailing underscore
    # don't override super.accounts, used indirectly by get_ibans_from_ribs
    accounts_ = URL(r"/distri-account-api/api/v1/persons/me/accounts", AccountsPage)
    balance = URL(r"/distri-account-api/api/v1/customers/me/accounts/(?P<account_id>.*)/balances", BalancePage)
    subscriptions = URL(r"/distri-account-api/api/v1/customers/me/accounts", SubscriptionsPage)
    documents = URL(r"/documentapi/api/v2/documents\?type=RELEVE$", DocumentsPage)
    document_pdf = URL(r"/documentapi/api/v2/documents/(?P<document_id>.*)/content\?database=(?P<database>.*)")
    rib_details = URL(r"/domiapi/oauth/json/accounts/recupererRib$", RibPage)
    transactions = URL(
        r"/distri-account-api/api/v1/persons/me/accounts/(?P<account_id>[A-Z0-9]{10})/transactions", TransactionsPage
    )

    def __init__(self, *args, **kwargs):
        # most of url return 403 without this origin header
        kwargs["origin"] = self.original_site
        super().__init__(*args, **kwargs)

    def code_challenge(self):
        """Generate a code challenge needed to go through the authorize end point
        and get a session id.
        Found in domi-auth-fat.js (45394)"""

        base = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        code_challenge = "".join(random.choices(base, k=39))
        return code_challenge

    def auth_state(self):
        """Generate a state needed to go through the authorize end point
        and get a session id.
        Found in domi-auth-fat.js (49981)"""

        base = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        state = "auth_" + "".join(random.choices(base, k=25))

        return state

    def code_verifier(self, code_challenge):
        """Generate a code verifier that will have to match the sha256 of the code_challenge
        on the server side.
        Found in domi-auth-fat.js (49986)"""

        digest = sha256(code_challenge.encode("utf-8")).digest()
        code_verifier = b64encode(digest)

        return code_verifier.decode()

    def get_pkce_codes(self):
        """Override parent (cf Axa).

        Returns code_verifier (/oauth/token), code_challenge (build_authorization_uri_params() / /oauth/authorize)
        """
        code_challenge = self.code_challenge()
        return self.code_verifier(code_challenge), code_challenge

    def build_authorization_uri_params(self):
        params = super().build_authorization_uri_params()
        params["state"] = self.auth_state()
        return params

    def build_request(self, *args, **kwargs):
        headers = kwargs.setdefault("headers", {})
        headers["x-apikey"] = self.arkea_client_id
        return super().build_request(*args, **kwargs)

    @need_login
    def get_subscription_list(self):
        params = {
            "types": "CHECKING",
            "roles": "TIT,COT",
        }
        self.subscriptions.go(params=params)
        return self.page.iter_subscriptions()

    @need_login
    def iter_documents(self, subscription):
        self.documents.go()
        return self.page.iter_documents(subid=subscription.id)

    @need_login
    def download_document(self, document):
        params = {"flattenDoc": False}
        return self.open(document.url, params=params).content

    def update_iban(self, account):
        self.rib_details.go(json={"numeroContratSouscritCrypte": account._index})
        iban_number = self.page.get_iban()
        if not account.iban:
            account.iban = iban_number

    def get_ibans_from_ribs(self):
        accounts_list = super().iter_accounts()
        for account in accounts_list:
            account._original_id = account.id
            self.update_iban(account)
        return {account.id: account.iban for account in accounts_list}

    @need_login
    def iter_accounts(self):
        ibans = self.get_ibans_from_ribs()

        go_accounts = retry(ClientError, tries=5)(self.accounts_.go)
        go_accounts(params={"types": "CHECKING,SAVING"})

        accounts_list = list(self.page.iter_accounts())
        for account in accounts_list:
            self.balance.go(account_id=account.id)
            balance = list(self.page.iter_balances())[0]
            account.balance = balance.amount
            account.iban = ibans.get(account.id)
            if account.iban:
                account.number = account.iban.account_code
            date_to = (date.today() + relativedelta(days=5)).strftime("%Y-%m-%dT11:00:00.000Z")
            self.balances_comings.go(account_id=account.id, params={"dateTo": date_to})
            self.page.fill_coming(account)

        return accounts_list

    @need_login
    def iter_history(self, account):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET, Account.TYPE_PEA):
            return super().iter_history(account)

        go_transactions = retry((ClientError, ServerError), tries=5)(self.transactions.go)
        go_transactions(account_id=account._original_id)
        return self.page.iter_transactions()


class CCFParBrowser(CCFBrowser):
    BASEURL = "https://api.ccf.fr"
    original_site = "https://mabanque.ccf.fr"
    SPACE = "PART"
    arkea_client_id = "JcqCF4MXkladWOKb4hRJGw7xEEuCFyXu"
    redirect_uri = "%s/auth/checkuser" % original_site
    error_uri = "%s/auth/errorauthn" % original_site


class CCFProBrowser(CCFBrowser):
    BASEURL = "https://api.cmb.fr"
    original_site = "https://pro.ccf.fr"
    SPACE = "PRO"
    arkea_client_id = "029Ao3yX6YRqbz9DtlSiIrFvgwuMBv9l"
    redirect_uri = "%s/auth/checkuser" % original_site
    error_uri = "%s/auth/errorauthn" % original_site
