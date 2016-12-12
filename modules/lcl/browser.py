# -*- coding: utf-8 -*-

# Copyright(C) 2010-2012  Romain Bignon, Pierre Mazière
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.


import urllib
from urlparse import urlsplit, parse_qsl

from weboob.exceptions import BrowserIncorrectPassword
from weboob.browser import LoginBrowser, URL, need_login
from weboob.browser.exceptions import ServerError
from weboob.capabilities.base import NotAvailable, find_object
from weboob.capabilities.bank import Account

from .pages import LoginPage, AccountsPage, AccountHistoryPage, \
                   CBListPage, CBHistoryPage, ContractsPage, BoursePage, \
                   AVPage, AVDetailPage, DiscPage, NoPermissionPage, RibPage, \
                   HomePage, LoansPage, TransferPage, RecipientPage


__all__ = ['LCLBrowser','LCLProBrowser']


# Browser
class LCLBrowser(LoginBrowser):
    BASEURL = 'https://particuliers.secure.lcl.fr'

    login = URL('/outil/UAUT/Authentication/authenticate',
                '/outil/UAUT\?from=.*',
                '/outil/UWER/Accueil/majicER',
                '/outil/UWER/Enregistrement/forwardAcc',
                LoginPage)
    contracts = URL('/outil/UAUT/Contrat/choixContrat.*',
                    '/outil/UAUT/Contract/getContract.*',
                    '/outil/UAUT/Contract/selectContracts.*',
                    '/outil/UAUT/Accueil/preRoutageLogin',
                    '.*outil/UAUT/Contract/routing',
                    ContractsPage)
    home = URL('/outil/UWHO/Accueil/', HomePage)
    accounts = URL('/outil/UWSP/Synthese', AccountsPage)
    history = URL('/outil/UWLM/ListeMouvements.*/accesListeMouvements.*',
                  '/outil/UWLM/DetailMouvement.*/accesDetailMouvement.*',
                  '/outil/UWLM/Rebond',
                  AccountHistoryPage)
    rib = URL('/outil/UWRI/Accueil/detailRib',
              '/outil/UWRI/Accueil/listeRib', RibPage)
    cb_list = URL('/outil/UWCB/UWCBEncours.*/listeCBCompte.*', CBListPage)
    cb_history = URL('/outil/UWCB/UWCBEncours.*/listeOperations.*', CBHistoryPage)
    skip = URL('/outil/UAUT/Contrat/selectionnerContrat.*',
               '/index.html')
    no_perm = URL('/outil/UAUT/SansDroit/affichePageSansDroit.*', NoPermissionPage)

    bourse = URL('https://bourse.secure.lcl.fr/netfinca-titres/servlet/com.netfinca.frontcr.synthesis.HomeSynthesis',
                 'https://bourse.secure.lcl.fr/netfinca-titres/servlet/com.netfinca.frontcr.account.*',
                 '/outil/UWBO.*', BoursePage)
    disc = URL('https://bourse.secure.lcl.fr/netfinca-titres/servlet/com.netfinca.frontcr.login.ContextTransferDisconnect',
               '/outil/UAUT/RetourPartenaire/retourCar', DiscPage)

    assurancevie = URL('/outil/UWVI/AssuranceVie/accesSynthese', AVPage)
    avdetail = URL('https://ASSURANCE-VIE-et-prevoyance.secure.lcl.fr.*',
                   'https://assurance-vie-et-prevoyance.secure.lcl.fr.*',
                   '/outil/UWVI/Routage', AVDetailPage)

    loans = URL('/outil/UWCR/SynthesePar/', LoansPage)

    transfer_page = URL('/outil/UWVS/', TransferPage)
    confirm_transfer = URL('/outil/UWVS/Accueil/redirectView', TransferPage)
    recipients = URL('/outil/UWBE/Consultation/list', RecipientPage)

    accounts_list = None

    def do_login(self):
        assert isinstance(self.username, basestring)
        assert isinstance(self.password, basestring)

        if not self.password.isdigit():
            raise BrowserIncorrectPassword()

        # we force the browser to go to login page so it's work even
        # if the session expire
        self.login.go()

        if not self.page.login(self.username, self.password) or \
           (self.login.is_here() and self.page.is_error()) :
            raise BrowserIncorrectPassword("invalid login/password.\nIf you did not change anything, be sure to check for password renewal request\non the original web site.\nAutomatic renewal will be implemented later.")

        self.accounts_list = None
        self.accounts.stay_or_go()

    @need_login
    def connexion_bourse(self):
        self.location('/outil/UWBO/AccesBourse/temporisationCar?codeTicker=TICKERBOURSECLI')
        if self.no_perm.is_here():
            return False
        self.location(self.page.get_next())
        self.bourse.stay_or_go()
        return True

    def deconnexion_bourse(self):
        self.disc.stay_or_go()
        self.page.come_back()
        self.page.come_back()

    @need_login
    def get_accounts_list(self):
        if self.accounts_list is None:
            self.accounts_list = []
            self.assurancevie.stay_or_go()
            if self.no_perm.is_here():
                self.logger.warning('Life insurances are unavailable.')
            else:
                for a in self.page.get_list():
                    self.accounts_list.append(a)
            self.accounts.stay_or_go()
            for a in self.page.get_list():
                self.location('/outil/UWRI/Accueil/')
                self.rib.go(data={'compte': '%s/%s/%s' % (a.id[0:5], a.id[5:11], a.id[11:])})
                if self.rib.is_here():
                    iban = self.page.get_iban()
                    a.iban = iban if iban and a.id[11:] in iban else NotAvailable
                self.accounts_list.append(a)
            self.loans.stay_or_go()
            if self.no_perm.is_here():
                self.logger.warning('Loans are unavailable.')
            else:
                for a in self.page.get_list():
                    self.accounts_list.append(a)
            if self.connexion_bourse():
                for a in self.page.get_list():
                    self.accounts_list.append(a)
                self.deconnexion_bourse()
                # Disconnecting from bourse portal before returning account list
                # to be sure that we are on the banque portal
        return iter(self.accounts_list)

    @need_login
    def get_history(self, account):
        if hasattr(account, '_market_link') and account._market_link:
            self.connexion_bourse()
            self.location(account._market_link)
            self.location(account._link_id).page.get_fullhistory()
            for tr in self.page.iter_history():
                yield tr
            self.deconnexion_bourse()
        elif hasattr(account, '_link_id') and account._link_id:
            try:
                self.location(account._link_id)
            except ServerError:
                return
            for tr in self.page.get_operations():
                yield tr
            for tr in self.get_cb_operations(account, 1):
                yield tr
        elif account.type == Account.TYPE_LIFE_INSURANCE and account._form:
            self.assurancevie.stay_or_go()
            account._form.submit()
            self.page.sub().page.sub().page.get_details(account, "OHIPU")
            for tr in self.page.iter_history():
                yield tr
            self.page.come_back().page.sub().page.come_back()

    @need_login
    def get_cb_operations(self, account, month=0):
        """
        Get CB operations.

        * month=0 : current operations (non debited)
        * month=1 : previous month operations (debited)
        """
        if not hasattr(account, '_coming_links'):
            return

        for link in account._coming_links:
            v = urlsplit(self.absurl(link))
            args = dict(parse_qsl(v.query))
            args['MOIS'] = month

            self.location('%s?%s' % (v.path, urllib.urlencode(args)))

            for tr in self.page.get_operations():
                yield tr

            for card_link in self.page.get_cards():
                self.location(card_link)
                for tr in self.page.get_operations():
                    yield tr

    @need_login
    def get_investment(self, account):
        if account.type == Account.TYPE_LIFE_INSURANCE and account._form:
            self.assurancevie.stay_or_go()
            account._form.submit()
            self.page.sub().page.sub()
            for inv in self.page.iter_investment():
                yield inv
            self.page.come_back().page.sub().page.come_back()
        elif hasattr(account, '_market_link') and account._market_link:
            self.connexion_bourse()
            for inv in self.location(account._market_link).page.iter_investment():
                yield inv
            self.deconnexion_bourse()

    @need_login
    def iter_recipients(self, origin_account):
        if origin_account._transfer_id is None:
            return
        self.transfer_page.go()
        if not self.page.can_transfer(origin_account._transfer_id):
            return
        for recipient in self.page.iter_recipients(account_transfer_id=origin_account._transfer_id):
            recipient.iban = find_object(self.get_accounts_list(), _transfer_id=recipient.id).iban
            yield recipient
        for recipient in self.recipients.go().iter_recipients():
            yield recipient

    @need_login
    def init_transfer(self, account, recipient, amount, reason=None):
        self.transfer_page.stay_or_go()
        self.page.transfer(account, recipient, amount, reason)
        self.confirm_transfer.go().check_data_consistency(account, recipient, amount, reason)
        return self.page.create_transfer(account, recipient, amount, reason)

    @need_login
    def execute_transfer(self, transfer):
        self.page.confirm(self.password)
        self.page.check_data_consistency(transfer._account, transfer._recipient, transfer.amount, transfer.label, 1)
        return self.page.fill_transfer_id(transfer)

class LCLProBrowser(LCLBrowser):
    BASEURL = 'https://professionnels.secure.lcl.fr'

    #We need to add this on the login form
    IDENTIFIANT_ROUTING = 'CLA'

    def __init__(self, *args, **kwargs):
        super(LCLProBrowser, self).__init__(*args, **kwargs)
        self.session.cookies.set("lclgen","professionnels")
