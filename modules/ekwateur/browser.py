# Copyright(C) 2018      Phyks (Lucas Verney)
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

import itertools


from woob.browser import LoginBrowser, need_login, URL
from woob.exceptions import BrowserIncorrectPassword

from .pages import BillsPage, DocumentsPage, LoginPage, ProfilePage


class EkwateurBrowser(LoginBrowser):
    BASEURL = 'https://mon-espace.ekwateur.fr/'

    login_page = URL('/se_connecter', LoginPage)
    bills_page = URL('/mes_factures_et_acomptes', BillsPage)
    documents_page = URL('/documents', DocumentsPage)
    profile = URL('/informations_personnelles', ProfilePage)

    def do_login(self):
        self.login_page.go().do_login(self.username, self.password)
        self.bills_page.stay_or_go()
        if not self.bills_page.is_here():
            raise BrowserIncorrectPassword

    @need_login
    def iter_subscriptions(self):
        return self.bills_page.stay_or_go().get_subscriptions()

    @need_login
    def iter_documents(self, sub_id):
        return itertools.chain(
            self.documents_page.stay_or_go().get_documents(sub_id=sub_id),
            self.documents_page.stay_or_go().get_cgv(sub_id),
            self.documents_page.stay_or_go().get_justificatif(sub_id),
            self.bills_page.stay_or_go().get_bills(sub_id=sub_id)
        )

    @need_login
    def get_profile(self):
        self.profile.go()
        return self.page.get_profile()
