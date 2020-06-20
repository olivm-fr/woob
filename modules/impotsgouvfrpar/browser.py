# -*- coding: utf-8 -*-

# Copyright(C) 2012-2020  Budget Insight
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

from weboob.browser import AbstractBrowser, URL, need_login
from weboob.exceptions import BrowserIncorrectPassword

from .pages import LoginAccessPage, LoginAELPage, ProfilePage, DocumentsPage, ThirdPartyDocPage


class ImpotsParBrowser(AbstractBrowser):
    BASEURL = 'https://cfspart.impots.gouv.fr'
    PARENT = 'franceconnect'

    login_access = URL(r'/LoginAccess', LoginAccessPage)
    login_ael = URL(r'/LoginAEL', LoginAELPage)
    third_party_doc_page = URL(r'/enp/ensu/dpr.do', ThirdPartyDocPage)

    # affichageadresse.do is pretty similar to chargementprofil.do but display address
    profile = URL(
        r'/enp/ensu/affichageadresse.do',
        r'/enp/?$',
        ProfilePage
    )
    documents = URL(r'/enp/ensu/documents.do', DocumentsPage)

    def __init__(self, login_source, *args, **kwargs):
        super(ImpotsParBrowser, self).__init__(*args, **kwargs)
        self.login_source = login_source

    def login_impots(self):
        self.page.login(self.username, self.password)

        msg = self.page.is_login_successful()
        if msg:
            raise BrowserIncorrectPassword(msg)

    def login_ameli(self):
        self.page.login(self.username, self.password)

        if self.ameli_wrong_login_page.is_here():
            raise BrowserIncorrectPassword(self.page.get_error_message())

    def france_connect_do_login(self):
        self.location('https://cfsfc.impots.gouv.fr/', data={'lmAuth': 'FranceConnect'})
        self.fc_call('dgfip', 'https://idp.impots.gouv.fr')
        self.login_impots()
        self.fc_redirect(self.page.get_redirect_url())
        # Needed to set cookies to be able to access profile page
        # without being disconnected
        self.location('https://cfsfc.impots.gouv.fr/enp/')

    def france_connect_ameli_do_login(self):
        self.location('https://cfsfc.impots.gouv.fr/', data={'lmAuth': 'FranceConnect'})
        self.fc_call('ameli', 'https://fc.assure.ameli.fr')
        self.login_ameli()
        self.fc_redirect()
        # Needed to set cookies to be able to access profile page
        # without being disconnected
        self.location('https://cfsfc.impots.gouv.fr/enp/')

    def do_login(self):
        if self.login_source == 'fc':
            self.france_connect_do_login()
            return

        if self.login_source == 'fc_ameli':
            self.france_connect_ameli_do_login()
            return

        self.login_access.go()
        self.login_impots()
        self.location(self.page.get_redirect_url())

    @need_login
    def iter_subscription(self):
        return self.profile.go().get_subscriptions()

    @need_login
    def iter_documents(self, subscription):
        # it's a document json which is used in the event of a declaration by a third party
        self.third_party_doc_page.go()
        yield self.page.get_third_party_doc()

        # put ?n=0, else website return an error page
        self.documents.go(params={'n': 0})
        for doc in self.page.iter_documents(subid=subscription.id):
            yield doc

    @need_login
    def get_profile(self):
        self.profile.go()
        return self.page.get_profile()
