# -*- coding: utf-8 -*-

# Copyright(C) 2017      Phyks (Lucas Verney)
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


from woob.browser import LoginBrowser, URL, need_login
from woob.browser.exceptions import ClientError
from woob.exceptions import BrowserIncorrectPassword

from .pages import LoginPage, BillsPage


class LampirisBrowser(LoginBrowser):
    BASEURL = 'https://espaceclient.total-spring.fr/'

    loginpage = URL('/user/login', LoginPage)
    billspage = URL('/factures-et-paiements', BillsPage)
    selectcus = URL('/set_selected_cus')

    def __init__(self, *args, **kwargs):
        self.logged = False
        super(LampirisBrowser, self).__init__(*args, **kwargs)

    def do_login(self):
        if self.logged:
            return
        self.loginpage.stay_or_go().do_login(self.username, self.password)

        try:
            self.billspage.go()
            self.logged = True
        except ClientError:
            raise BrowserIncorrectPassword()

    @need_login
    def get_subscriptions(self):
        return self.billspage.go().get_subscriptions()

    @need_login
    def get_documents(self, subscription):
        # Select subscription
        self.selectcus.go(params={'cus': subscription.id})

        # Then, fetch documents
        for doc in self.billspage.go().get_documents():
            doc.id = "{}#{}".format(subscription.id, doc.id)
            yield doc
