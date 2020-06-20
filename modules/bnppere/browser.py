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

from __future__ import unicode_literals

from weboob.browser import AbstractBrowser, LoginBrowser, URL, need_login
from weboob.capabilities.bank import Account
from weboob.capabilities.wealth import Per
from weboob.exceptions import BrowserIncorrectPassword, BrowserUnavailable, ActionNeeded
from .pages import (
    LoginPage, LoginErrorPage, ProfilePage, ErrorPage, AccountPage, AccountSwitchPage,
    InvestmentPage, TermPage, UnexpectedPage, HistoryPage,
)


class BnppereBrowser(AbstractBrowser):
    PARENT = 's2e'
    PARENT_ATTR = 'package.browser.BnppereBrowser'


class VisiogoBrowser(LoginBrowser):
    BASEURL = 'https://visiogo.bnpparibas.com/'

    login_page = URL(r'https://authentication.bnpparibas.com/en/Account/Login\?ReturnUrl=https://visiogo.bnpparibas.com/fr-FR', LoginPage)
    login_error = URL(r'https://authentication.bnpparibas.com.*ErrorNoValidAffiliation', LoginErrorPage)
    error_page = URL(r'https://authentication.bnpparibas.com/en/account/login\?ReturnUrl=.+', ErrorPage)
    error_page2 = URL(r'https://authentication.bnpparibas.com/Error\?Code=500', UnexpectedPage)
    term_page = URL(r'/Home/TermsOfUseApproval', TermPage)
    account_page = URL(r'/GlobalView/Synthesis', AccountPage)
    account_switch = URL(r'/Contract/_ChangeAffiliation', AccountSwitchPage)
    investment_page = URL(r'/Saving/Details', InvestmentPage)
    profile_page = URL(r'/en/Profile/EditContactDetails', ProfilePage)
    history_page = URL(r'/en/Operation/History', HistoryPage)

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
        self.multi_accounts = False
        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()
        super(VisiogoBrowser, self).__init__(*args, **kwargs)

    def do_login(self):
        self.login_page.go()
        self.page.login(self.username, self.password)

        if self.login_error.is_here():
            message = self.page.get_message()
            if 'affiliation status' in message:
                # 'Your affiliation status no longer allows you to connect to your account.'
                raise ActionNeeded(message)
            assert False, 'Unknown error on LoginErrorPage: %s.' % message

        if self.term_page.is_here():
            raise ActionNeeded()

        if self.error_page.is_here() or self.error_page2.is_here():
            alert = self.page.get_error()
            if "account has not been activated" in alert:
                raise ActionNeeded(alert)
            elif "unexpected" in alert:
                raise BrowserUnavailable(alert)
            elif "password" in alert:
                raise BrowserIncorrectPassword(alert)
            else:
                assert False

    @need_login
    def iter_accounts(self):
        self.account_page.go()
        accounts_list = []

        for account in self.page.iter_accounts():
            if account.type == Account.TYPE_PER:
                per = Per.from_dict(account.to_dict())
                per._sublabel = account._sublabel
                self.page.fill_per(obj=per)
                accounts_list.append(per)
            else:
                accounts_list.append(account)

        # We need to know if there are several accounts
        # in order to handle their investments properly
        if len(accounts_list) > 1:
            self.multi_accounts = True
            # In order to access an account's detail, we must determine its index
            # in the list, but the order on investment_page is not the same as on
            # account_page, so we must get the account indices on investment_page.
            self.investment_page.go()
            for account in accounts_list:
                account._index = self.page.get_account_index(account._sublabel)
        return accounts_list

    def iter_investment(self, account):
        if self.multi_accounts:
            # Access details of the right account
            self.account_switch.go(
                data={'index': account._index}
            )
        self.investment_page.go()
        return self.page.iter_investments()

    def iter_history(self, account):
        if self.multi_accounts:
            # Access details of the right account
            self.account_switch.go(
                data={'index': account._index}
            )
        self.history_page.go()
        return self.page.iter_history()

    def iter_pocket(self, account):
        raise NotImplementedError()

    def get_profile(self):
        self.profile_page.go()
        return self.page.get_profile()
