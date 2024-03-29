# -*- coding: utf-8 -*-

# Copyright(C) 2012 Romain Bignon
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

from weboob.browser import LoginBrowser, URL, need_login
from weboob.exceptions import BrowserIncorrectPassword, BrowserPasswordExpired, ActionNeeded, BrowserUnavailable
from weboob.capabilities.bank import Account
from weboob.capabilities.base import find_object
from weboob.tools.capabilities.bank.investments import create_french_liquidity
from weboob.tools.compat import urlencode
from .pages import (
    LoginPage, ProfilePage, AccountTypePage, AccountsPage, ProAccountsPage,
    TransactionsPage, IbanPage, RedirectPage, EntryPage, AVPage, ProIbanPage,
    ProTransactionsPage, LabelsPage, RgpdPage, LoginConfirmPage, ZDBPage
)


class CreditDuNordBrowser(LoginBrowser):
    ENCODING = 'UTF-8'
    BASEURL = "https://www.credit-du-nord.fr/"

    login = URL('$',
                '/.*\?.*_pageLabel=page_erreur_connexion',
                '/.*\?.*_pageLabel=reinitialisation_mot_de_passe',
                LoginPage)
    logout = URL('/pkmslogout')
    login_confirm = URL(r'/sec/vk/authent.json', LoginConfirmPage)
    labels_page = URL(r'/icd/zco/data/public-menu.json', LabelsPage)
    redirect = URL('/swm/redirectCDN.html', RedirectPage)
    entrypage = URL(r'/icd/zco/$', '/icd/zco/public-index.html#zco', EntryPage)
    multitype_av = URL('/vos-comptes/IPT/appmanager/transac/professionnels\?_nfpb=true&_eventName=onRestart&_pageLabel=synthese_contrats_assurance_vie', AVPage)
    loans = URL(r'/vos-comptes/IPT/appmanager/transac/(?P<account_type>.*)\?_nfpb=true&_eventName=onRestart&_pageLabel=(?P<loans_page_label>(creditPersoImmobilier|credit_?_en_cours))', ProAccountsPage)
    proaccounts = URL(r'/vos-comptes/IPT/appmanager/transac/(professionnels|entreprises)\?_nfpb=true&_eventName=onRestart&_pageLabel=(?P<accounts_page_label>(transac_tableau_de_bord|page_?_synthese_v1))',
                      r'/vos-comptes/(professionnels|entreprises)/page_?_synthese',
                      ProAccountsPage)
    accounts = URL(r'/vos-comptes/IPT/appmanager/transac/(?P<account_type>.*)\?_nfpb=true&_eventName=onRestart&_pageLabel=(?P<accounts_page_label>(transac_tableau_de_bord|page_?_synthese_v1))',
                   r'/vos-comptes/particuliers',
                   AccountsPage)
    multitype_iban = URL('/vos-comptes/IPT/appmanager/transac/professionnels\?_nfpb=true&_eventName=onRestart&_pageLabel=impression_rib', ProIbanPage)
    transactions = URL('/vos-comptes/IPT/appmanager/transac/particuliers\?_nfpb=true(.*)', TransactionsPage)
    protransactions = URL('/vos-comptes/(.*)/transac/(professionnels|entreprises)', ProTransactionsPage)
    iban = URL('/vos-comptes/IPT/cdnProxyResource/transacClippe/RIB_impress.asp', IbanPage)
    account_type_page = URL('/icd/zco/data/public-ws-menuespaceperso.json', AccountTypePage)
    labels_page = URL("/icd/zco/public-data/ws-menu.json", LabelsPage)
    profile_page = URL("/icd/zco/data/public-user.json", ProfilePage)
    bypass_rgpd = URL('/icd/zcd/data/gdpr-get-out-zs-client.json', RgpdPage)
    zdb = URL('/icd/zdb/index.html', ZDBPage)
    authsec = URL('/swm/swm-scaw-authsec.html', ZDBPage)
    connect = URL('/swm/swm-connect.html', ZDBPage)

    def __init__(self, *args, **kwargs):
        self.weboob = kwargs['weboob']
        super(CreditDuNordBrowser, self).__init__(*args, **kwargs)

    @property
    def logged(self):
        return self.page is not None and not self.login.is_here() and \
            not self.page.doc.xpath(u'//b[contains(text(), "vous devez modifier votre code confidentiel")]')

    def do_login(self):
        self.zdb.go()
        gdareplay = self.page.get_gdareplay_html()

        self.login.go()

        # Some users are still using their old password, that leads to a virtual keyboard crash.
        if not self.password.isdigit() or len(self.password) != 6:
            raise BrowserIncorrectPassword('Veuillez utiliser le nouveau code confidentiel fourni par votre banque.')

        self.page.login(self.username, self.password)

        assert self.login_confirm.is_here(), 'Should be on login confirmation page'

        if self.page.get_status() != 'ok':
            raise BrowserIncorrectPassword()
        reason = self.page.get_reason()
        if reason == 'chgt_mdp_oblig':
            # There is no message in the json return. There is just the code.
            raise BrowserPasswordExpired('Changement de mot de passe requis.')
        elif reason == 'SCA':
            raise ActionNeeded("Vous devez réaliser la double authentification sur le portail internet")
        elif reason == 'SCAW':
            self.zdb.go()
            gdareplay2 = self.page.get_gdareplay_form()
            self.session.cookies.set('SCAW', 'true')
            self.authsec.go(data=urlencode(gdareplay2), method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})
            self.connect.go(data=urlencode(gdareplay), method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})
            #raise ActionNeeded("Vous devez choisir si vous souhaitez dès à présent activer la double authentification sur le portail internet")

        self.entrypage.go()

    def do_logout(self):
        self.logout.go()
        self.session.cookies.clear()

    def _iter_accounts(self):
        owner_name = self.get_profile().name.upper()

        self.location(self.loans.build(account_type=self.account_type, loans_page_label=self.loans_page_label), allow_redirects=False)
        location = self.response.headers.get('Location', '')
        if 'errorWebCDN' in location:
            # Attempts to access to ProAccountsPage can lead instead to RedirectPage.
            # It would end up to a '/sites/erreur-404' URL (but in code 200).
            # This happens only on certain connections, as a wrongly activated
            # security feature of the server, as discussed directly with the bank,
            # when there is no accounts on ProAccountsPage.
            # Redirection is not followed
            # but the whole session is broken; need to log back
            self.do_logout()
            self.do_login()
        else:
            if location:
                # still preserve any other redirection that might occur
                self.location(location)
            #for a in self.page.get_list():
            #    yield a

        self.accounts.go(account_type=self.account_type, accounts_page_label=self.accounts_page_label)
        self.multitype_av.go()
        if self.multitype_av.is_here():
            for a in self.page.get_av_accounts():
                self.location(a._link, data=a._args)
                self.location(a._link.replace("_attente", "_detail_contrat_rep"), data=a._args)
                if self.page.get_error():
                    raise BrowserUnavailable(self.page.get_error())
                self.page.fill_diff_currency(a)
                yield a
        self.accounts.go(account_type=self.account_type, accounts_page_label=self.accounts_page_label)
        if self.accounts.is_here():
            for a in self.page.get_list(name=owner_name):
                yield a
        else:
            for a in self.page.get_list():
                yield a

    @need_login
    def get_pages_labels(self):
        # When retrieving labels_page,
        # If GDPR was accepted partially the website throws a page that we treat
        # as an ActionNeeded. Sometime we can by-pass it. Hence this fix
        try:
            self.labels_page.go()
        except ActionNeeded:
            self.bypass_rgpd.go()
            self.labels_page.go()
        return self.page.get_labels()

    @need_login
    def get_accounts_list(self):
        self.accounts_page_label, self.loans_page_label =  self.get_pages_labels()
        self.account_type_page.go()
        self.account_type = self.page.get_account_type()

        accounts = list(self._iter_accounts())
        self.multitype_iban.go()
        link = self.page.iban_go()

        if link:
            # For some accounts, the IBAN is displayed somewhere else behind
            # an OTP validation (icd/zco/public-index.html#zco/transac/impression_rib),
            # the link is None if this is the case.
            # TODO when we will be able to test this OTP
            for a in accounts:
                if a._acc_nb and a.type != Account.TYPE_CARD:
                    self.location(link + a._acc_nb)
                    a.iban = self.page.get_iban()

        return accounts

    def get_account(self, id):
        account_list = self.get_accounts_list()
        return find_object(account_list, id=id)

    @need_login
    def get_account_for_history(self, id):
        account_list = list(self._iter_accounts())
        return find_object(account_list, id=id)

    @need_login
    def iter_transactions(self, account):
        args = account._args
        if args is None:
            return
        while args is not None:
            self.location(account._link, data=args)
            assert (self.transactions.is_here() or self.protransactions.is_here())
            for tr in self.page.get_history(account):
                yield tr

            args = self.page.get_next_args(args)

    @need_login
    def get_history(self, account, coming=False):
        if coming and account.type != Account.TYPE_CARD or account.type == Account.TYPE_LOAN:
            return
        for tr in self.iter_transactions(account):
            yield tr

    @need_login
    def get_investment(self, account):
        if 'LIQUIDIT' in account.label:
            return [create_french_liquidity(account.balance)]

        if not account._inv:
            return []

        if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
            self.location(account._link, data=account._args)
            if self.page.can_iter_investments() and self.page.not_restrained():
                return self.page.get_market_investment()

        elif account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_CAPITALISATION):
            self.location(account._link, data=account._args)
            self.location(account._link.replace("_attente", "_detail_contrat_rep"), data=account._args)
            if self.page.can_iter_investments():
                return self.page.get_li_investments()
        return []

    @need_login
    def get_profile(self):
        self.profile_page.go()
        return self.page.get_profile()
