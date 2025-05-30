# Copyright(C) 2013-2014  Fourcot Florent
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
from woob.exceptions import BrowserIncorrectPassword

from .pages import BillsPage, ErrorPage, HistoryPage, HomePage, LoginPage


__all__ = ["PoivyBrowser"]


class PoivyBrowser(LoginBrowser):
    BASEURL = "https://www.poivy.com"

    login = URL("/login", LoginPage)
    homepage = URL("/buy_credit.*", HomePage)
    history = URL("/recent_calls", HistoryPage)
    bills = URL("/purchases", BillsPage)
    warning = URL("/warning.*", ErrorPage)

    def do_login(self):
        assert isinstance(self.username, str)
        assert isinstance(self.password, str)

        self.login.stay_or_go()

        self.page.login(self.username, self.password)

        if self.login.is_here() or self.warning.is_here():
            raise BrowserIncorrectPassword()

    @need_login
    def get_subscription_list(self):
        return self.homepage.stay_or_go().get_list()

    @need_login
    def get_history(self):
        self.history.stay_or_go()
        return self.page.get_calls()
