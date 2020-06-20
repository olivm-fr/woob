# -*- coding: utf-8 -*-

# Copyright(C) 2016       Baptiste Delpey
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

# flake8: compatible

from __future__ import unicode_literals

import requests

from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from weboob.browser.retry import login_method, retry_on_logout, RetryLoginBrowser
from weboob.browser.browsers import need_login, TwoFactorBrowser
from weboob.browser.url import URL
from weboob.exceptions import BrowserIncorrectPassword, BrowserHTTPNotFound, NoAccountsException, BrowserUnavailable
from weboob.browser.exceptions import LoggedOut, ClientError
from weboob.capabilities.bank import (
    Account, AccountNotFound, TransferError, TransferInvalidAmount,
    TransferInvalidEmitter, TransferInvalidLabel, TransferInvalidRecipient,
    AddRecipientStep, Rate, TransferBankError, AccountOwnership, RecipientNotFound,
    AddRecipientTimeout, TransferDateType, Emitter,
)
from weboob.capabilities.base import empty, find_object
from weboob.capabilities.contact import Advisor
from weboob.tools.value import Value
from weboob.tools.compat import basestring, urlsplit
from weboob.tools.capabilities.bank.transactions import sorted_transactions
from weboob.tools.capabilities.bank.bank_transfer import sorted_transfers

from .pages import (
    VirtKeyboardPage, AccountsPage, AsvPage, HistoryPage, AuthenticationPage,
    MarketPage, LoanPage, SavingMarketPage, ErrorPage, IncidentPage, IbanPage, ProfilePage, ExpertPage,
    CardsNumberPage, CalendarPage, HomePage, PEPPage,
    TransferAccounts, TransferRecipients, TransferCharac, TransferConfirm, TransferSent,
    AddRecipientPage, StatusPage, CardHistoryPage, CardCalendarPage, CurrencyListPage, CurrencyConvertPage,
    AccountsErrorPage, NoAccountPage, TransferMainPage, PasswordPage,
)
from .transfer_pages import TransferListPage, TransferInfoPage


__all__ = ['BoursoramaBrowser']


class BrowserIncorrectAuthenticationCode(BrowserIncorrectPassword):
    pass


class BoursoramaBrowser(RetryLoginBrowser, TwoFactorBrowser):
    BASEURL = 'https://clients.boursorama.com'
    TIMEOUT = 60.0
    HAS_CREDENTIALS_ONLY = True
    TWOFA_DURATION = 60 * 24 * 90

    home = URL('/$', HomePage)
    keyboard = URL(r'/connexion/clavier-virtuel\?_hinclude=1', VirtKeyboardPage)
    status = URL(r'/aide/messages/dashboard\?showza=0&_hinclude=1', StatusPage)
    calendar = URL('/compte/cav/.*/calendrier', CalendarPage)
    card_calendar = URL('https://api.boursorama.com/services/api/files/download.phtml.*', CardCalendarPage)
    error = URL(
        '/connexion/compte-verrouille',
        '/infos-profil',
        ErrorPage
    )
    login = URL(r'/connexion/saisie-mot-de-passe/', PasswordPage)

    accounts = URL(r'/dashboard/comptes\?_hinclude=300000', AccountsPage)
    accounts_error = URL(r'/dashboard/comptes\?_hinclude=300000', AccountsErrorPage)
    pro_accounts = URL(r'/dashboard/comptes-professionnels\?_hinclude=1', AccountsPage)
    no_account = URL(
        r'/dashboard/comptes\?_hinclude=300000',
        r'/dashboard/comptes-professionnels\?_hinclude=1',
        NoAccountPage
    )

    history = URL(r'/compte/(cav|epargne)/(?P<webid>.*)/mouvements.*', HistoryPage)
    card_transactions = URL('/compte/cav/(?P<webid>.*)/carte/.*', HistoryPage)
    deffered_card_history = URL('https://api.boursorama.com/services/api/files/download.phtml.*', CardHistoryPage)
    budget_transactions = URL('/budget/compte/(?P<webid>.*)/mouvements.*', HistoryPage)
    other_transactions = URL('/compte/cav/(?P<webid>.*)/mouvements.*', HistoryPage)
    saving_transactions = URL('/compte/epargne/csl/(?P<webid>.*)/mouvements.*', HistoryPage)
    saving_pep = URL('/compte/epargne/pep', PEPPage)
    incident = URL('/compte/cav/(?P<webid>.*)/mes-incidents.*', IncidentPage)

    # transfer
    transfer_list = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/suivi/(?P<type>\w+)$',
        # next url is for pagination, token is very long
        # make sure you don't match "details" or it could break "transfer_info" URL
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/suivi/(?P<type>\w+)/[a-zA-Z0-9]{30,}$',
        TransferListPage
    )
    transfer_info = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/suivi/(?P<type>\w+)/details/[\w-]{40,}',
        TransferInfoPage
    )
    transfer_main_page = URL(r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements$', TransferMainPage)
    transfer_accounts = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/nouveau$',
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/1',
        TransferAccounts
    )
    recipients_page = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements$',
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/2',
        TransferRecipients
    )
    transfer_charac = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/3',
        TransferCharac
    )
    transfer_confirm = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/4',
        TransferConfirm
    )
    transfer_sent = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/5',
        TransferSent
    )
    rcpt_page = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/comptes-externes/nouveau/(?P<id>\w+)/\d',
        AddRecipientPage
    )

    asv = URL('/compte/assurance-vie/.*', AsvPage)
    saving_history = URL(
        '/compte/cefp/.*/(positions|mouvements)',
        '/compte/.*ord/.*/mouvements',
        '/compte/pea/.*/mouvements',
        '/compte/0%25pea/.*/mouvements',
        '/compte/pea-pme/.*/mouvements',
        SavingMarketPage
    )
    market = URL(
        r'/compte/(?!assurance|cav|epargne).*/(positions|mouvements|ordres)',
        r'/compte/ord/.*/positions',
        MarketPage
    )
    loans = URL(
        r'/credit/paiement-3x/.*/informations',
        r'/credit/immobilier/.*/informations',
        r'/credit/immobilier/.*/caracteristiques',
        r'/credit/consommation/.*/informations',
        r'/credit/lombard/.*/caracteristiques',
        LoanPage
    )
    authentication = URL('/securisation', AuthenticationPage)
    iban = URL('/compte/(?P<webid>.*)/rib', IbanPage)
    profile = URL('/mon-profil/', ProfilePage)
    profile_children = URL('/mon-profil/coordonnees/enfants', ProfilePage)

    expert = URL('/compte/derive/', ExpertPage)

    cards = URL('/compte/cav/cb', CardsNumberPage)

    currencylist = URL('https://www.boursorama.com/bourse/devises/parite/_detail-parite', CurrencyListPage)
    currencyconvert = URL(
        'https://www.boursorama.com/bourse/devises/convertisseur-devises/convertir',
        CurrencyConvertPage
    )

    __states__ = ('auth_token', 'recipient_form',)

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
        self.auth_token = None
        self.accounts_list = None
        self.cards_list = None
        self.deferred_card_calendar = None
        self.recipient_form = None
        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()

        self.AUTHENTICATION_METHODS = {
            'pin_code': self.handle_sms,
        }

        super(BoursoramaBrowser, self).__init__(config, *args, **kwargs)

    def locate_browser(self, state):
        try:
            self.location(state['url'])
        except (requests.exceptions.HTTPError, requests.exceptions.TooManyRedirects, LoggedOut):
            pass

    def load_state(self, state):
        # needed to continue the session while adding recipient with otp
        # it keeps the form to continue to submit the otp
        if state.get('recipient_form'):
            state.pop('url', None)

        super(BoursoramaBrowser, self).load_state(state)

    def handle_authentication(self):
        if self.authentication.is_here():
            self.check_interactive()

            confirmation_link = self.page.get_confirmation_link()
            if confirmation_link:
                self.location(confirmation_link)

            self.page.sms_first_step()
            self.page.sms_second_step()

    def handle_sms(self):
        # regular 2FA way
        if self.auth_token:
            self.page.authenticate()
        # PSD2 way
        else:
            # we can't access form without sending a SMS again
            self.location(
                '/securisation/authentification/validation',
                data={
                    'strong_authentication_confirm[code]': self.config['pin_code'].get(),
                    'strong_authentication_confirm[type]': 'brs-otp-sms',
                }
            )

        if self.authentication.is_here():
            raise BrowserIncorrectAuthenticationCode()

    def init_login(self):
        self.login.go()
        self.page.enter_password(self.username, self.password)

        if self.error.is_here():
            raise BrowserIncorrectPassword()
        elif self.login.is_here():
            error = self.page.get_error()
            assert error, 'Should not be on login page without error message'

            wrongpass_messages = (
                'Identifiant ou mot de passe invalide',
                "Erreur d'authentification",
                "Cette valeur n'est pas valide",
                "votre identifiant ou votre mot de passe n'est pas valide",
            )

            if 'vous pouvez actuellement rencontrer des difficultés pour accéder à votre Espace Client' in error:
                raise BrowserUnavailable()
            elif any(msg in error for msg in wrongpass_messages):
                raise BrowserIncorrectPassword(error)

            raise AssertionError('Unhandled error message : "%s"' % error)

        # After login, we might be redirected to the two factor authentication page.
        self.handle_authentication()

    @login_method
    def do_login(self):
        return super(BoursoramaBrowser, self).do_login()

    def ownership_guesser(self):
        ownerless_accounts = [account for account in self.accounts_list if empty(account.ownership)]

        # On Boursorama website, all mandatory accounts have the real owner name in their label, and
        # children names are findable in the PSU profile.
        self.profile_children.go()
        children_names = self.page.get_children_firstnames()

        for ownerless_account in ownerless_accounts:
            for child_name in children_names:
                if child_name in ownerless_account.label:
                    ownerless_account.ownership = AccountOwnership.ATTORNEY
                    break

        # If there are two deferred card for with the same parent account, we assume that's the parent checking
        # account is a 'CO_OWNER' account
        parent_accounts = []
        for account in self.accounts_list:
            if account.type == Account.TYPE_CARD and empty(account.parent.ownership):
                if account.parent in parent_accounts:
                    account.parent.ownership = AccountOwnership.CO_OWNER
                parent_accounts.append(account.parent)

        # We set all accounts without ownership as if they belong to the credential owner
        for account in self.accounts_list:
            if empty(account.ownership) and account.type != Account.TYPE_CARD:
                account.ownership = AccountOwnership.OWNER

        # Account cards should be set with the same ownership of their parents accounts
        for account in self.accounts_list:
            if account.type == Account.TYPE_CARD:
                account.ownership = account.parent.ownership

    def go_cards_number(self, link):
        self.location(link)
        self.location(self.page.get_cards_number_link())

    @retry_on_logout()
    @need_login
    def get_accounts_list(self):
        self.status.go()

        exc = None
        for _ in range(3):
            if self.accounts_list is not None:
                break

            self.accounts_list = []
            self.loans_list = []
            # Check that there is at least one account for this user
            has_account = False
            self.pro_accounts.go()
            if self.pro_accounts.is_here():
                self.accounts_list.extend(self.page.iter_accounts())
                has_account = True
            else:
                # We dont want to let has_account=False if we landed on an unknown page
                # it has to be the no_accounts page
                assert self.no_account.is_here()

            try:
                self.accounts.go()
            except BrowserUnavailable as e:
                self.logger.warning('par accounts seem unavailable, retrying')
                exc = e
                self.accounts_list = None
                continue
            else:
                if self.accounts.is_here():
                    self.accounts_list.extend(self.page.iter_accounts())
                    has_account = True
                else:
                    # We dont want to let has_account=False if we landed on an unknown page
                    # it has to be the no_accounts page
                    assert self.no_account.is_here()

                exc = None

            if not has_account:
                # if we landed twice on NoAccountPage, it means there is neither pro accounts nor pp accounts
                raise NoAccountsException()

            for account in list(self.accounts_list):
                if account.type == Account.TYPE_LOAN:
                    # Loans details are present on another page so we create
                    # a Loan object and remove the corresponding Account:
                    self.location(account.url)
                    loan = self.page.get_loan()
                    loan.url = account.url
                    self.loans_list.append(loan)
                    self.accounts_list.remove(account)
            self.accounts_list.extend(self.loans_list)

            self.cards_list = [acc for acc in self.accounts_list if acc.type == Account.TYPE_CARD]
            if self.cards_list:
                self.go_cards_number(self.cards_list[0].url)
                if self.cards.is_here():
                    self.page.populate_cards_number(self.cards_list)
            # Cards without a number are not activated yet:
            for card in self.cards_list:
                if not card.number:
                    self.accounts_list.remove(card)

            type_with_iban = (
                Account.TYPE_CHECKING,
                Account.TYPE_SAVINGS,
                Account.TYPE_MARKET,
                Account.TYPE_PEA,
            )
            for account in self.accounts_list:
                if account.type in type_with_iban:
                    account.iban = self.iban.go(webid=account._webid).get_iban()

            for card in self.cards_list:
                checking, = [
                    account
                    for account in self.accounts_list
                    if account.type == Account.TYPE_CHECKING and account.url in card.url
                ]
                card.parent = checking

        if exc:
            raise exc

        self.ownership_guesser()
        return self.accounts_list

    def get_account(self, id):
        assert isinstance(id, basestring)

        for a in self.get_accounts_list():
            if a.id == id:
                return a
        return None

    def get_debit_date(self, debit_date):
        for i, j in zip(self.deferred_card_calendar, self.deferred_card_calendar[1:]):
            if i[0] < debit_date <= j[0]:
                return j[1]

    @retry_on_logout()
    @need_login
    def get_history(self, account, coming=False):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_CONSUMER_CREDIT) or '/compte/derive' in account.url:
            return []
        if account.type is Account.TYPE_SAVINGS and u"PLAN D'ÉPARGNE POPULAIRE" in account.label:
            return []
        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET):
            return self.get_invest_transactions(account, coming)
        elif account.type == Account.TYPE_CARD:
            return self.get_card_transactions(account, coming)
        return self.get_regular_transactions(account, coming)

    def get_regular_transactions(self, account, coming):
        if not coming:
            # We look for 3 years of history.
            params = {}
            params['movementSearch[toDate]'] = (date.today() + relativedelta(days=40)).strftime('%d/%m/%Y')
            params['movementSearch[fromDate]'] = (date.today() - relativedelta(years=3)).strftime('%d/%m/%Y')
            params['movementSearch[selectedAccounts][]'] = account._webid
            self.location('%s/mouvements' % account.url.rstrip('/'), params=params)
            for transaction in self.page.iter_history():
                yield transaction

        # Note: Checking accounts have a 'Mes prélèvements à venir' tab,
        # but these transactions have no date anymore so we ignore them.

    def get_card_transactions(self, account, coming):
        # All card transactions can be found in the CSV (history and coming),
        # however the CSV shows a maximum of 1000 transactions from all accounts.
        self.location(account.url)
        if self.home.is_here():
            # for some cards, the site redirects us to '/'...
            return

        if self.deferred_card_calendar is None:
            self.location(self.page.get_calendar_link())

        params = {}
        params['movementSearch[fromDate]'] = (date.today() - relativedelta(years=3)).strftime('%d/%m/%Y')
        params['fullSearch'] = 1
        self.location(account.url, params=params)
        csv_link = self.page.get_csv_link()
        if csv_link:
            self.location(csv_link)
            # Yield past transactions as 'history' and
            # transactions in the future as 'coming':
            for tr in sorted_transactions(self.page.iter_history(account_number=account.number)):
                if coming and tr.date > date.today():
                    tr._is_coming = True
                    yield tr
                elif not coming and tr.date <= date.today():
                    yield tr

    def get_invest_transactions(self, account, coming):
        if coming:
            return
        transactions = []
        self.location('%s/mouvements' % account.url.rstrip('/'))
        account._history_pages = []
        for t in self.page.iter_history(account=account):
            transactions.append(t)
        for t in self.page.get_transactions_from_detail(account):
            transactions.append(t)
        for t in sorted(transactions, key=lambda tr: tr.date, reverse=True):
            yield t

    @retry_on_logout()
    @need_login
    def iter_investment(self, account):
        if (
            '/compte/derive' in account.url
            or account.type not in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET, Account.TYPE_PEA)
        ):
            return []
        self.location(account.url)
        return self.page.iter_investment()

    @retry_on_logout()
    @need_login
    def iter_market_orders(self, account):
        # Only Market & PEA accounts have the Market Orders tab
        if '/compte/derive' in account.url or account.type not in (Account.TYPE_MARKET, Account.TYPE_PEA):
            return []
        self.location(account.url)

        # Go to Market Orders tab ('Mes ordres')
        market_order_link = self.page.get_market_order_link()
        if not market_order_link:
            self.logger.warning('Could not find market orders link for account "%s".', account.label)
            return []
        self.location(market_order_link)
        return self.page.iter_market_orders()

    @need_login
    def get_profile(self):
        return self.profile.stay_or_go().get_profile()

    @need_login
    def get_advisor(self):
        # same for everyone
        advisor = Advisor()
        advisor.name = u"Service clientèle"
        advisor.phone = u"0146094949"
        return iter([advisor])

    def go_recipients_list(self, account_url, account_id):
        # url transfer preparation
        url = urlsplit(account_url)
        parts = [part for part in url.path.split('/') if part]

        assert len(parts) > 2, 'Account url missing some important part to iter recipient'
        account_type = parts[1]  # cav, ord, epargne ...
        account_webid = parts[-1]

        self.transfer_main_page.go(acc_type=account_type, webid=account_webid)  # may raise a BrowserHTTPNotFound

        # can check all account available transfer option
        if self.transfer_main_page.is_here():
            self.transfer_accounts.go(acc_type=account_type, webid=account_webid)

        if self.transfer_accounts.is_here():
            self.page.submit_account(account_id)  # may raise AccountNotFound

    @need_login
    def iter_transfer_recipients(self, account):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_LIFE_INSURANCE):
            return []
        assert account.url

        try:
            self.go_recipients_list(account.url, account.id)
        except (BrowserHTTPNotFound, AccountNotFound):
            return []

        assert self.recipients_page.is_here()
        return self.page.iter_recipients()

    def check_basic_transfer(self, transfer):
        if transfer.amount <= 0:
            raise TransferInvalidAmount('transfer amount must be positive')
        if transfer.recipient_id == transfer.account_id:
            raise TransferInvalidRecipient('recipient must be different from emitter')
        if not transfer.label:
            raise TransferInvalidLabel('transfer label cannot be empty')

    @need_login
    def init_transfer(self, transfer, **kwargs):
        self.check_basic_transfer(transfer)

        account = self.get_account(transfer.account_id)
        if not account:
            raise AccountNotFound()

        recipients = list(self.iter_transfer_recipients(account))
        if not recipients:
            raise TransferInvalidEmitter('The account cannot emit transfers')

        recipients = [rcpt for rcpt in recipients if rcpt.id == transfer.recipient_id]
        if len(recipients) == 0:
            raise TransferInvalidRecipient('The recipient cannot be used with the emitter account')
        assert len(recipients) == 1

        self.page.submit_recipient(recipients[0]._tempid)
        assert self.transfer_charac.is_here()

        self.page.submit_info(transfer.amount, transfer.label, transfer.exec_date)
        assert self.transfer_confirm.is_here()

        if self.page.need_refresh():
            # In some case we are not yet in the transfer_charac page, you need to refresh the page
            self.location(self.url)
            assert not self.page.need_refresh()
        ret = self.page.get_transfer()

        # at this stage, the site doesn't show the real ids/ibans, we can only guess
        if recipients[0].label != ret.recipient_label:
            self.logger.info(
                'Recipients from iter_recipient and from the transfer are diffent: "%s" and "%s"',
                recipients[0].label, ret.recipient_label
            )
            if not ret.recipient_label.startswith('%s - ' % recipients[0].label):
                # the label displayed here is  "<name> - <bank>"
                # but in the recipients list it is "<name>"...
                raise AssertionError(
                    'Recipient label changed during transfer (from "%s" to "%s")'
                    % (recipients[0].label, ret.recipient_label)
                )
        ret.recipient_id = recipients[0].id
        ret.recipient_iban = recipients[0].iban

        if account.label != ret.account_label:
            raise TransferError('Account label changed during transfer')

        ret.account_id = account.id
        ret.account_iban = account.iban

        return ret

    @need_login
    def execute_transfer(self, transfer, **kwargs):
        assert self.transfer_confirm.is_here()
        self.page.submit()

        assert self.transfer_sent.is_here()
        transfer_error = self.page.get_transfer_error()
        if transfer_error:
            raise TransferBankError(message=transfer_error)

        # the last page contains no info, return the last transfer object from init_transfer
        return transfer

    @need_login
    def init_new_recipient(self, recipient):
        self.recipient_form = None  # so it is reset when a new recipient is added

        # get url
        account = None
        for account in self.get_accounts_list():
            if account.url:
                break

        suffix = 'virements/comptes-externes/nouveau'
        if account.url.endswith('/'):
            target = account.url + suffix
        else:
            target = account.url + '/' + suffix

        self.location(target)
        assert self.page.is_charac(), 'Not on the page to add recipients.'

        # fill recipient form
        self.page.submit_recipient(recipient)
        recipient.origin_account_id = account.id

        # confirm sending sms
        assert self.page.is_confirm_send_sms(), 'Cannot reach the page asking to send a sms.'
        self.page.confirm_send_sms()

        if self.page.is_send_sms():
            # send sms
            self.page.send_otp()
            assert self.page.is_confirm_otp(), 'The sms was not sent.'

            self.recipient_form = self.page.get_confirm_otp_form()
            self.recipient_form['account_url'] = account.url
            raise AddRecipientStep(recipient, Value('otp_sms', label='Veuillez saisir le code recu par sms'))

        # if the add recipient is restarted after the sms has been confirmed recently, the sms step is not presented again
        return self.rcpt_after_sms()

    def new_recipient(self, recipient, **kwargs):
        # step 2 of new_recipient
        if 'otp_sms' in kwargs:
            # there is no confirmation to check the recipient
            # validating the sms code directly adds the recipient
            account_url = self.send_recipient_form(kwargs['otp_sms'])
            return self.rcpt_after_sms(recipient, account_url)
        # step 3 of new_recipient (not always used)
        elif 'otp_email' in kwargs:
            account_url = self.send_recipient_form(kwargs['otp_email'])
            return self.check_and_update_recipient(recipient, account_url)

        # step 1 of new recipient
        return self.init_new_recipient(recipient)

    def send_recipient_form(self, value):
        if not self.recipient_form:
            # The session expired
            raise AddRecipientTimeout()

        url = self.recipient_form.pop('url')
        account_url = self.recipient_form.pop('account_url')
        self.recipient_form['strong_authentication_confirm[code]'] = value
        self.location(url, data=self.recipient_form)

        self.recipient_form = None
        return account_url

    def rcpt_after_sms(self, recipient, account_url):
        if self.page.is_send_email():
            # Sometimes after validating the sms code, the user is also
            # asked to validate a code received by email (observed when
            # adding a non-french recipient).
            self.page.send_otp()
            assert self.page.is_confirm_otp(), 'The email was not sent.'

            self.recipient_form = self.page.get_confirm_otp_form()
            self.recipient_form['account_url'] = account_url
            raise AddRecipientStep(recipient, Value('otp_email', label='Veuillez saisir le code recu par email'))

        return self.check_and_update_recipient(recipient, account_url)

    def check_and_update_recipient(self, recipient, account_url):
        assert self.page.is_created(), 'The recipient was not added.'

        # At this point, the recipient was added to the website,
        # here we just want to return the right Recipient object.
        # We are taking it from the recipient list page
        # because there is no summary of the adding
        self.go_recipients_list(account_url, recipient.origin_account_id)
        return find_object(self.page.iter_recipients(), id=recipient.id, error=RecipientNotFound)

    @need_login
    def iter_transfers(self, account):
        if account is not None:
            if not (isinstance(account, Account) or isinstance(account, Emitter)):
                self.logger.debug('we have only the emitter id %r, fetching full object', account)
                account = find_object(self.iter_emitters(), id=account)

            return sorted_transfers(self.iter_transfers_for_emitter(account))

        transfers = []
        self.logger.debug('no account given: fetching all emitters')
        for emitter in self.iter_emitters():
            self.logger.debug('fetching transfers for emitter %r', emitter.id)
            transfers.extend(self.iter_transfers_for_emitter(emitter))
        transfers = sorted_transfers(transfers)
        return transfers

    @need_login
    def iter_transfers_for_emitter(self, emitter):
        # We fetch original transfers from 2 pages (single transfers vs periodic).
        # Each page is sorted, but since we list from the 2 pages in sequence,
        # the result is not sorted as is.
        # TODO Maybe the site is not stateful and we could do parallel navigation
        # on both lists, to merge the sorted iterators.

        self.transfer_list.go(acc_type='temp', webid=emitter._bourso_id, type='ponctuels')
        for transfer in self.page.iter_transfers():
            transfer.account_id = emitter.id
            transfer.date_type = TransferDateType.FIRST_OPEN_DAY
            if transfer._is_instant:
                transfer.date_type = TransferDateType.INSTANT
            elif transfer.exec_date > date.today():
                # The site does not indicate when transfer was created
                # we only have the date of its execution.
                # So, for a DONE transfer, we cannot know if it was deferred or not...
                transfer.date_type = TransferDateType.DEFERRED

            self.location(transfer.url)
            self.page.fill_transfer(obj=transfer)

            # build id with account id because get_transfer will receive only the account id
            assert transfer.id, 'transfer should have an id from site'
            transfer.id = '%s.%s' % (emitter.id, transfer.id)
            yield transfer

        self.transfer_list.go(acc_type='temp', webid=emitter._bourso_id, type='permanents')
        for transfer in self.page.iter_transfers():
            transfer.account_id = emitter.id
            transfer.date_type = TransferDateType.PERIODIC
            self.location(transfer.url)
            self.page.fill_transfer(obj=transfer)
            self.page.fill_periodic_transfer(obj=transfer)

            assert transfer.id, 'transfer should have an id from site'
            transfer.id = '%s.%s' % (emitter.id, transfer.id)
            yield transfer

    def iter_currencies(self):
        return self.currencylist.go().get_currency_list()

    def get_rate(self, curr_from, curr_to):
        r = Rate()
        params = {
            'from': curr_from,
            'to': curr_to,
            'amount': '1',
        }
        r.currency_from = curr_from
        r.currency_to = curr_to
        r.datetime = datetime.now()
        try:
            self.currencyconvert.go(params=params)
            r.value = self.page.get_rate()
        # if a rate is no available the site return a 401 error...
        except ClientError:
            return
        return r

    @need_login
    def iter_emitters(self):
        # It seems that if we give a wrong acc_type and webid to the transfer page
        # we are redirected to a page where we can choose the emitter account
        self.transfer_accounts.go(acc_type='temp', webid='temp')
        return self.page.iter_emitters()
