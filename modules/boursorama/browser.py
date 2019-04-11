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


import requests
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from dateutil import parser

from weboob.browser.retry import login_method, retry_on_logout, RetryLoginBrowser
from weboob.browser.browsers import need_login, StatesMixin
from weboob.browser.url import URL
from weboob.exceptions import BrowserIncorrectPassword, BrowserHTTPNotFound, NoAccountsException, BrowserUnavailable
from weboob.browser.exceptions import LoggedOut, ClientError
from weboob.capabilities.bank import (
    Account, AccountNotFound, TransferError, TransferInvalidAmount,
    TransferInvalidEmitter, TransferInvalidLabel, TransferInvalidRecipient,
    AddRecipientStep, Recipient, Rate, TransferBankError,
)
from weboob.capabilities.contact import Advisor
from weboob.tools.captcha.virtkeyboard import VirtKeyboardError
from weboob.tools.value import Value
from weboob.tools.compat import basestring, urlsplit
from weboob.tools.capabilities.bank.transactions import sorted_transactions

from .pages import (
    LoginPage, VirtKeyboardPage, AccountsPage, AsvPage, HistoryPage, AuthenticationPage,
    MarketPage, LoanPage, SavingMarketPage, ErrorPage, IncidentPage, IbanPage, ProfilePage, ExpertPage,
    CardsNumberPage, CalendarPage, HomePage, PEPPage,
    TransferAccounts, TransferRecipients, TransferCharac, TransferConfirm, TransferSent,
    AddRecipientPage, StatusPage, CardHistoryPage, CardCalendarPage, CurrencyListPage, CurrencyConvertPage,
    AccountsErrorPage, NoAccountPage, TransferMainPage,
)


__all__ = ['BoursoramaBrowser']


class BrowserIncorrectAuthenticationCode(BrowserIncorrectPassword):
    pass


class BoursoramaBrowser(RetryLoginBrowser, StatesMixin):
    BASEURL = 'https://clients.boursorama.com'
    TIMEOUT = 60.0
    STATE_DURATION = 10

    home = URL('/$', HomePage)
    keyboard = URL('/connexion/clavier-virtuel\?_hinclude=300000', VirtKeyboardPage)
    status = URL(r'/aide/messages/dashboard\?showza=0&_hinclude=1', StatusPage)
    calendar = URL('/compte/cav/.*/calendrier', CalendarPage)
    card_calendar = URL('https://api.boursorama.com/services/api/files/download.phtml.*', CardCalendarPage)
    error = URL('/connexion/compte-verrouille',
                '/infos-profil', ErrorPage)
    login = URL('/connexion/', LoginPage)

    accounts = URL('/dashboard/comptes\?_hinclude=300000', AccountsPage)
    accounts_error = URL('/dashboard/comptes\?_hinclude=300000', AccountsErrorPage)
    pro_accounts = URL(r'/dashboard/comptes-professionnels\?_hinclude=1', AccountsPage)
    no_account = URL('/dashboard/comptes\?_hinclude=300000',
                     '/dashboard/comptes-professionnels\?_hinclude=1', NoAccountPage)

    history = URL('/compte/(cav|epargne)/(?P<webid>.*)/mouvements.*',  HistoryPage)
    card_transactions = URL('/compte/cav/(?P<webid>.*)/carte/.*', HistoryPage)
    deffered_card_history = URL('https://api.boursorama.com/services/api/files/download.phtml.*', CardHistoryPage)
    budget_transactions = URL('/budget/compte/(?P<webid>.*)/mouvements.*', HistoryPage)
    other_transactions = URL('/compte/cav/(?P<webid>.*)/mouvements.*', HistoryPage)
    saving_transactions = URL('/compte/epargne/csl/(?P<webid>.*)/mouvements.*', HistoryPage)
    saving_pep = URL('/compte/epargne/pep',  PEPPage)
    incident = URL('/compte/cav/(?P<webid>.*)/mes-incidents.*', IncidentPage)

    # transfer
    transfer_main_page = URL(r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements$', TransferMainPage)
    transfer_accounts = URL(r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/nouveau$',
                            r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/1', TransferAccounts)
    recipients_page = URL(r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements$',
                          r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/2',
                          TransferRecipients)
    transfer_charac = URL(r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/3',
                          TransferCharac)
    transfer_confirm = URL(r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/4',
                           TransferConfirm)
    transfer_sent = URL(r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/5',
                        TransferSent)
    rcpt_page = URL(r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/comptes-externes/nouveau/(?P<id>\w+)/\d',
                    AddRecipientPage)

    asv = URL('/compte/assurance-vie/.*', AsvPage)
    saving_history = URL('/compte/cefp/.*/(positions|mouvements)',
                         '/compte/.*ord/.*/mouvements',
                         '/compte/pea/.*/mouvements',
                         '/compte/0%25pea/.*/mouvements',
                         '/compte/pea-pme/.*/mouvements', SavingMarketPage)
    market = URL('/compte/(?!assurance|cav|epargne).*/(positions|mouvements)',
                 '/compte/ord/.*/positions', MarketPage)
    loans = URL('/credit/immobilier/.*/informations',
                '/credit/immobilier/.*/caracteristiques',
                '/credit/consommation/.*/informations',
                '/credit/lombard/.*/caracteristiques', LoanPage)
    authentication = URL('/securisation', AuthenticationPage)
    iban = URL('/compte/(?P<webid>.*)/rib', IbanPage)
    profile = URL('/mon-profil/', ProfilePage)

    expert = URL('/compte/derive/', ExpertPage)

    cards = URL('/compte/cav/cb', CardsNumberPage)

    currencylist = URL('https://www.boursorama.com/bourse/devises/parite/_detail-parite', CurrencyListPage)
    currencyconvert = URL('https://www.boursorama.com/bourse/devises/convertisseur-devises/convertir', CurrencyConvertPage)

    __states__ = ('auth_token',)

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
        self.auth_token = None
        self.accounts_list = None
        self.cards_list = None
        self.deferred_card_calendar = None
        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()
        super(BoursoramaBrowser, self).__init__(*args, **kwargs)

    def locate_browser(self, state):
        try:
            self.location(state['url'])
        except (requests.exceptions.HTTPError, requests.exceptions.TooManyRedirects, LoggedOut):
            pass

    def load_state(self, state):
        if ('expire' in state and parser.parse(state['expire']) > datetime.now()) or state.get('auth_token'):
            return super(BoursoramaBrowser, self).load_state(state)

    def handle_authentication(self):
        if self.authentication.is_here():
            if self.config['enable_twofactors'].get():
                self.page.sms_first_step()
                self.page.sms_second_step()
            else:
                raise BrowserIncorrectAuthenticationCode(
                    """Boursorama - activate the two factor authentication in boursorama config."""
                    """ You will receive SMS code but are limited in request per day (around 15)"""
                )

    @login_method
    def do_login(self):
        assert isinstance(self.config['device'].get(), basestring)
        assert isinstance(self.config['enable_twofactors'].get(), bool)
        if not self.password.isalnum():
            raise BrowserIncorrectPassword()

        if self.auth_token and self.config['pin_code'].get():
            self.page.authenticate()
        else:
            for _ in range(3):
                self.login.go()
                try:
                    self.page.login(self.username, self.password)
                except VirtKeyboardError:
                    self.logger.error('Failed to process VirtualKeyboard')
                else:
                    break
            else:
                raise VirtKeyboardError()

            if self.login.is_here() or self.error.is_here():
                raise BrowserIncorrectPassword()

            # After login, we might be redirected to the two factor authentication page.
            self.handle_authentication()

        if self.authentication.is_here():
            raise BrowserIncorrectAuthenticationCode('Invalid PIN code')

    def go_cards_number(self, link):
        self.location(link)
        self.location(self.page.get_cards_number_link())

    @retry_on_logout()
    @need_login
    def get_accounts_list(self):
        self.status.go()

        exc = None
        for x in range(3):
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

            for account in self.accounts_list:
                if account.type not in (Account.TYPE_CARD, Account.TYPE_LOAN, Account.TYPE_CONSUMER_CREDIT, Account.TYPE_MORTGAGE, Account.TYPE_REVOLVING_CREDIT, Account.TYPE_LIFE_INSURANCE):
                    account.iban = self.iban.go(webid=account._webid).get_iban()

            for card in self.cards_list:
                checking, = [account for account in self.accounts_list if account.type == Account.TYPE_CHECKING and account.url in card.url]
                card.parent = checking

        if exc:
            raise exc

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
        # We look for 3 years of history.
        params = {}
        params['movementSearch[toDate]'] = (date.today() + relativedelta(days=40)).strftime('%d/%m/%Y')
        params['movementSearch[fromDate]'] = (date.today() - relativedelta(years=3)).strftime('%d/%m/%Y')
        params['movementSearch[selectedAccounts][]'] = account._webid
        self.location('%s/mouvements' % account.url.rstrip('/'), params=params)
        for t in self.page.iter_history():
                yield t
        if coming and account.type == Account.TYPE_CHECKING:
            self.location('%s/mouvements-a-venir' % account.url.rstrip('/'), params=params)
            for t in self.page.iter_history(coming=True):
                yield t

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
                elif not coming and tr.date < date.today():
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

    @need_login
    def get_investment(self, account):
        if '/compte/derive' in account.url:
            return iter([])
        if not account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET, Account.TYPE_PEA):
            raise NotImplementedError()
        self.location(account.url)
        # We might deconnect at this point.
        if self.login.is_here():
            return self.get_investment(account)
        return self.page.iter_investment()

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

    @need_login
    def iter_transfer_recipients(self, account):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_LIFE_INSURANCE):
            return []
        assert account.url

        # url transfer preparation
        url = urlsplit(account.url)
        parts = [part for part in url.path.split('/') if part]

        assert len(parts) > 2, 'Account url missing some important part to iter recipient'
        account_type = parts[1] # cav, ord, epargne ...
        account_webid = parts[-1]

        try:
            self.transfer_main_page.go(acc_type=account_type, webid=account_webid)
        except BrowserHTTPNotFound:
            return []

        # can check all account available transfer option
        if self.transfer_main_page.is_here():
            self.transfer_accounts.go(acc_type=account_type, webid=account_webid)

        if self.transfer_accounts.is_here():
            try:
                self.page.submit_account(account.id)
            except AccountNotFound:
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
            if not recipients[0].label.startswith('%s - ' % ret.recipient_label):
                # the label displayed here is just "<name>"
                # but in the recipients list it is "<name> - <bank>"...
                raise TransferError('Recipient label changed during transfer')
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
            raise TransferBankError(transfer_error)

        # the last page contains no info, return the last transfer object from init_transfer
        return transfer

    def build_recipient(self, recipient):
        r = Recipient()
        r.iban = recipient.iban
        r.id = recipient.iban
        r.label = recipient.label
        r.category = recipient.category
        r.enabled_at = date.today()
        r.currency = u'EUR'
        r.bank_name = recipient.bank_name
        return r

    @need_login
    def new_recipient(self, recipient, **kwargs):
        if 'code' in kwargs:
            assert self.rcpt_page.is_here()
            assert self.page.is_confirm_sms()

            self.page.confirm_sms(kwargs['code'])
            return self.rcpt_after_sms()

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
        assert self.page.is_charac()

        self.page.submit_recipient(recipient)

        if self.page.is_send_sms():
            self.page.send_sms()
            assert self.page.is_confirm_sms()
            raise AddRecipientStep(self.build_recipient(recipient), Value('code', label='Veuillez saisir le code'))
        # if the add recipient is restarted after the sms has been confirmed recently, the sms step is not presented again

        return self.rcpt_after_sms()

    def rcpt_after_sms(self):
        assert self.page.is_confirm()

        ret = self.page.get_recipient()
        self.page.confirm()

        assert self.page.is_created()
        return ret

    def iter_currencies(self):
        return self.currencylist.go().get_currency_list()

    def get_rate(self, curr_from, curr_to):
        r = Rate()
        params = {
            'from': curr_from,
            'to': curr_to,
            'amount': '1'
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
