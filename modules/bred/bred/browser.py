# -*- coding: utf-8 -*-

# Copyright(C) 2014 Romain Bignon
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

import json
import time
import operator
import random
import string
from datetime import date
from decimal import Decimal

from weboob.exceptions import (
    AuthMethodNotImplemented, AppValidation,
    AppValidationExpired, AppValidationCancelled,
    BrowserQuestion, BrowserIncorrectPassword,
)
from weboob.capabilities.bank import (
    Account, AddRecipientStep, AddRecipientBankError,
    TransferBankError,
)
from weboob.browser import need_login, URL
from weboob.browser.browsers import TwoFactorBrowser
from weboob.browser.exceptions import ClientError
from weboob.capabilities.base import find_object
from weboob.tools.capabilities.bank.investments import create_french_liquidity
from weboob.tools.capabilities.bank.transactions import sorted_transactions
from weboob.tools.value import Value

from .linebourse_browser import LinebourseAPIBrowser
from .pages import (
    HomePage, LoginPage, AccountsTwoFAPage, InitAuthentPage, AuthentResultPage,
    SendSmsPage, CheckOtpPage, TrustedDevicesPage, UniversePage,
    TokenPage, MoveUniversePage, SwitchPage,
    LoansPage, AccountsPage, IbanPage, LifeInsurancesPage,
    SearchPage, ProfilePage, EmailsPage, ErrorPage,
    ErrorCodePage, LinebourseLoginPage,
)
from .transfer_pages import (
    RecipientListPage, EmittersListPage, ListAuthentPage,
    AddRecipientPage, TransferPage,
)


__all__ = ['BredBrowser']


class BredBrowser(TwoFactorBrowser):
    BASEURL = 'https://www.bred.fr'
    HAS_CREDENTIALS_ONLY = True

    LINEBOURSE_BROWSER = LinebourseAPIBrowser

    home = URL(r'/$', HomePage)
    login = URL(r'/transactionnel/Authentication', LoginPage)
    error = URL(r'.*gestion-des-erreurs/erreur-pwd',
                r'.*gestion-des-erreurs/opposition',
                r'/pages-gestion-des-erreurs/erreur-technique',
                r'/pages-gestion-des-erreurs/message-tiers-oppose', ErrorPage)
    universe = URL(r'/transactionnel/services/applications/menu/getMenuUnivers', UniversePage)
    token = URL(r'/transactionnel/services/rest/User/nonce\?random=(?P<timestamp>.*)', TokenPage)
    move_universe = URL(r'/transactionnel/services/applications/listes/(?P<key>.*)/default', MoveUniversePage)
    switch = URL(r'/transactionnel/services/rest/User/switch', SwitchPage)
    loans = URL(r'/transactionnel/services/applications/prets/liste', LoansPage)
    accounts = URL(r'/transactionnel/services/rest/Account/accounts', AccountsPage)
    iban = URL(r'/transactionnel/services/rest/Account/account/(?P<number>.*)/iban', IbanPage)
    linebourse_login = URL(r'/transactionnel/v2/services/applications/SSO/linebourse', LinebourseLoginPage)
    life_insurances = URL(r'/transactionnel/services/applications/avoirsPrepar/getAvoirs', LifeInsurancesPage)
    search = URL(r'/transactionnel/services/applications/operations/getSearch/', SearchPage)
    profile = URL(r'/transactionnel/services/rest/User/user', ProfilePage)
    emails = URL(r'/transactionnel/services/applications/gestionEmail/getAdressesMails', EmailsPage)
    error_code = URL(r'/.*\?errorCode=.*', ErrorCodePage)

    accounts_twofa = URL(r'/transactionnel/v2/services/rest/Account/accounts', AccountsTwoFAPage)
    list_authent = URL(r'/transactionnel/services/applications/authenticationstrong/listeAuthent/(?P<context>\w+)', ListAuthentPage)
    init_authent = URL(r'/transactionnel/services/applications/authenticationstrong/init', InitAuthentPage)
    authent_result = URL(r'/transactionnel/services/applications/authenticationstrong/result/(?P<authent_id>[^/]+)/(?P<context>\w+)', AuthentResultPage)
    trusted_devices = URL(r'/transactionnel/services/applications/trustedDevices', TrustedDevicesPage)
    check_otp = URL(r'/transactionnel/services/applications/authenticationstrong/(?P<auth_method>\w+)/check', CheckOtpPage)
    send_sms = URL(r'/transactionnel/services/applications/authenticationstrong/sms/send', SendSmsPage)

    recipient_list = URL(r'/transactionnel/v2/services/applications/virement/getComptesCrediteurs', RecipientListPage)
    emitters_list = URL(r'/transactionnel/v2/services/applications/virement/getComptesDebiteurs', EmittersListPage)

    add_recipient = URL(r'/transactionnel/v2/services/applications/beneficiaires/updateBeneficiaire', AddRecipientPage)

    create_transfer = URL(r'/transactionnel/v2/services/applications/virement/confirmVirement', TransferPage)
    validate_transfer = URL(r'/transactionnel/v2/services/applications/virement/validVirement', TransferPage)

    __states__ = (
        'auth_method', 'need_reload_state', 'authent_id', 'device_id',
        'context', 'recipient_transfer_limit',
    )

    def __init__(self, accnum, config, *args, **kwargs):
        self.config = config
        kwargs['username'] = self.config['login'].get()
        self.weboob = kwargs['weboob']

        # Bred only use first 8 char (even if the password is set to be bigger)
        # The js login form remove after 8th char. No comment.
        kwargs['password'] = self.config['password'].get()[:8]

        super(BredBrowser, self).__init__(config, *args, **kwargs)

        self.accnum = accnum
        self.universes = None
        self.current_univers = None
        self.need_reload_state = None
        self.context = None
        self.device_id = None
        self.auth_method = None
        self.authent_id = None
        self.recipient_transfer_limit = None

        # Some accounts are detailed on linebourse. The only way to know which is to go on linebourse.
        # The parameters to do so depend on the universe.
        self.linebourse_urls = {}
        self.linebourse_tokens = {}
        dirname = self.responses_dirname
        if dirname:
            dirname += '/bourse'
        self.linebourse = self.LINEBOURSE_BROWSER(
            'https://www.linebourse.fr',
            logger=self.logger,
            responses_dirname=dirname,
            weboob=self.weboob,
            proxy=self.PROXIES,
        )

        self.AUTHENTICATION_METHODS = {
            'resume': self.handle_polling,  # validation in mobile app
            'otp_sms': self.handle_otp_sms,  # OTP in SMS
            'otp_app': self.handle_otp_app,  # OTP in mobile app
        }

    def load_state(self, state):
        if state.get('need_reload_state') or state.get('device_id'):
            state.pop('url', None)
            super(BredBrowser, self).load_state(state)
            self.need_reload_state = None

    def init_login(self):
        if self.device_id:
            # will not raise 2FA if the one realized with this id is still valid
            self.session.headers['x-trusted-device-id'] = self.device_id

        if 'hsess' not in self.session.cookies:
            self.home.go()  # set session token
            assert 'hsess' in self.session.cookies, "Session token not correctly set"

        # hard-coded authentication payload
        data = {
            'identifiant': self.username,
            'password': self.password,
        }
        self.login.go(data=data)

        try:
            # It's an accounts page if SCA already done
            # Need to first go there to trigger it, since LoginPage doesn't do that.
            self.accounts_twofa.go()
        except ClientError as e:
            if e.response.status_code == 449:
                self.check_interactive()
                self.context = e.response.json()['content']
                self.trigger_connection_twofa()
            raise

    def trigger_connection_twofa(self):
        # Needed to record the device doing the SCA and keep it valid.
        self.device_id = ''.join(random.choices(string.digits, k=50))

        self.auth_method = self.get_connection_twofa_method()

        if self.auth_method == 'notification':
            self.update_headers()
            data = {
                'context': self.context['contextAppli'],  # 'accounts_access'
                'type_auth': 'NOTIFICATION',
                'type_phone': 'P',
            }
            self.init_authent.go(json=data)
            self.authent_id = self.page.get_authent_id()
            raise AppValidation(self.context['message'])

        elif self.auth_method == 'sms':
            self.update_headers()
            data = {
                'context': self.context['context'],
                'contextAppli': self.context['contextAppli'],
            }
            self.send_sms.go(json=data)
            raise BrowserQuestion(
                Value('otp_sms', label=self.context['message']),
            )

        elif self.auth_method == 'otp':
            raise BrowserQuestion(
                Value('otp_app', label=self.context['message']),
            )

    def get_connection_twofa_method(self):
        message = self.context['message']
        # Order is important: there can be 'notification' and 'otp' true at the same time,
        # but it will prioritized by BRED as an AppValidation
        auth_methods = ('notification', 'sms', 'otp')
        for auth_method in auth_methods:
            if self.context['liste'].get(auth_method):
                return auth_method
        raise AuthMethodNotImplemented('Unhandled strong authentification method: %s' % message)

    def update_headers(self):
        timestamp = int(time.time() * 1000)
        if self.device_id:
            self.session.headers['x-trusted-device-id'] = self.device_id
        self.token.go(timestamp=timestamp)
        self.session.headers['x-token-bred'] = self.page.get_content()

    def handle_polling(self, enrol=True):
        for _ in range(60):  # 5' timeout duration on website
            self.update_headers()
            self.authent_result.go(
                authent_id=self.authent_id,
                context=self.context['contextAppli'],
                json={},  # yes, needed
            )

            status = self.page.get_status()
            if not status:
                # When the notification expires, we get a generic error message
                # instead of a status like 'PENDING'
                self.context = None
                raise AppValidationExpired('La validation par application mobile a expiré.')

            elif status == 'ABORTED':
                self.context = None
                raise AppValidationCancelled("La validation par application a été annulée par l'utilisateur.")

            elif status == 'AUTHORISED':
                self.context = None
                if enrol:
                    self.enrol_device()
                return

            assert status == 'PENDING', "Unhandled app validation status : '%s'" % status
            time.sleep(5)

        self.context = None
        raise AppValidationExpired('La validation par application mobile a expiré.')

    def handle_otp_sms(self):
        self.validate_connection_otp_auth(self.otp_sms)

    def handle_otp_app(self):
        self.validate_connection_otp_auth(self.otp_app)

    def validate_connection_otp_auth(self, auth_value):
        self.update_headers()
        data = {
            'context': self.context['context'],
            'contextAppli': self.context['contextAppli'],
            'otp': auth_value,
        }
        self.check_otp.go(
            auth_method=self.auth_method,
            json=data,
        )

        error = self.page.get_error()
        if error:
            raise BrowserIncorrectPassword('Error when validating OTP: %s' % error)

        self.context = None
        self.enrol_device()

    def enrol_device(self):
        # Add device_id to list of trusted devices to avoid SCA for 90 days
        # User will see a 'BI' entry on this list and can delete it on demand.
        self.update_headers()
        data = {
            'uuid': self.device_id,  # Called an uuid but it's just a 50 digits long string.
            'deviceName': 'Accès BudgetInsight pour agrégation',  # clear message for user
            'biometricEnabled': False,
            'securedBiometricEnabled': False,
            'notificationEnabled': False,
        }
        self.trusted_devices.go(json=data)

        error = self.page.get_error()
        if error:
            raise BrowserIncorrectPassword('Error when enroling trusted device: %s' % error)

    @need_login
    def get_universes(self):
        """Get universes (particulier, pro, etc)"""
        self.update_headers()
        self.universe.go(headers={'Accept': 'application/json'})

        return self.page.get_universes()

    def move_to_universe(self, univers):
        if univers == self.current_univers:
            return
        self.move_universe.go(key=univers)
        self.update_headers()
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        self.switch.go(
            data=json.dumps({'all': 'false', 'univers': univers}),
            headers=headers,
        )
        self.current_univers = univers

    @need_login
    def get_accounts_list(self):
        accounts = []
        for universe_key in self.get_universes():
            self.move_to_universe(universe_key)
            universe_accounts = []
            universe_accounts.extend(self.get_list())
            universe_accounts.extend(self.get_life_insurance_list(accounts))
            universe_accounts.extend(self.get_loans_list())
            linebourse_accounts = self.get_linebourse_accounts(universe_key)
            for account in universe_accounts:
                account._is_in_linebourse = False
                # Accound id looks like 'bred_account_id.folder_id'
                # We only want bred_account_id and we need to clean it to match it to linebourse IDs.
                account_id = account.id.strip('0').split('.')[0]
                for linebourse_account in linebourse_accounts:
                    if account_id in linebourse_account:
                        account._is_in_linebourse = True
            accounts.extend(universe_accounts)

        # Life insurances are sometimes in multiple universes, we have to remove duplicates
        unique_accounts = {account.id: account for account in accounts}
        return sorted(unique_accounts.values(), key=operator.attrgetter('_univers'))

    @need_login
    def get_linebourse_accounts(self, universe_key):
        self.move_to_universe(universe_key)
        if universe_key not in self.linebourse_urls:
            self.linebourse_login.go()
            if self.linebourse_login.is_here():
                linebourse_url = self.page.get_linebourse_url()
                if linebourse_url:
                    self.linebourse_urls[universe_key] = linebourse_url
                    self.linebourse_tokens[universe_key] = self.page.get_linebourse_token()
        if universe_key in self.linebourse_urls:
            self.linebourse.location(
                self.linebourse_urls[universe_key],
                data={'SJRToken': self.linebourse_tokens[universe_key]}
            )
            self.linebourse.session.headers['X-XSRF-TOKEN'] = self.linebourse.session.cookies.get('XSRF-TOKEN')
            params = {'_': '{}'.format(int(time.time() * 1000))}
            self.linebourse.account_codes.go(params=params)
            if self.linebourse.account_codes.is_here():
                return self.linebourse.page.get_accounts_list()
        return []

    @need_login
    def get_loans_list(self):
        self.loans.go()
        return self.page.iter_loans(current_univers=self.current_univers)

    @need_login
    def get_list(self):
        self.accounts.go()
        for acc in self.page.iter_accounts(accnum=self.accnum, current_univers=self.current_univers):
            yield acc

    @need_login
    def get_life_insurance_list(self, accounts):

        self.life_insurances.go()

        for ins in self.page.iter_lifeinsurances(univers=self.current_univers):
            ins.parent = find_object(accounts, _number=ins._parent_number, type=Account.TYPE_CHECKING)
            yield ins

    @need_login
    def _make_api_call(self, account, start_date, end_date, offset, max_length=50):
        self.update_headers()
        call_payload = {
            "account": account._number,
            "poste": account._nature,
            "sousPoste": account._codeSousPoste or '00',
            "devise": account.currency,
            "fromDate": start_date.strftime('%Y-%m-%d'),
            "toDate": end_date.strftime('%Y-%m-%d'),
            "from": offset,
            "size": max_length,  # max length of transactions
            "search": "",
            "categorie": "",
        }
        self.search.go(json=call_payload)
        return self.page.get_transaction_list()

    @need_login
    def get_history(self, account, coming=False):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_LIFE_INSURANCE) or not account._consultable:
            raise NotImplementedError()

        if account._univers != self.current_univers:
            self.move_to_universe(account._univers)

        today = date.today()
        seen = set()
        offset = 0
        total_transactions = 0
        next_page = True
        end_date = date.today()
        last_date = None
        while next_page:
            if offset == 10000:
                offset = 0
                end_date = last_date
            operation_list = self._make_api_call(
                account=account,
                start_date=date(day=1, month=1, year=2000), end_date=end_date,
                offset=offset, max_length=50,
            )

            transactions = self.page.iter_history(account=account, operation_list=operation_list, seen=seen, today=today, coming=coming)

            transactions = sorted_transactions(transactions)
            if transactions:
                last_date = transactions[-1].date
            # Transactions are unsorted
            for t in transactions:
                if coming == t._coming:
                    yield t
                elif coming and not t._coming:
                    # coming transactions are at the top of history
                    self.logger.debug('stopping coming after %s', t)
                    return

            next_page = len(transactions) > 0
            offset += 50
            total_transactions += 50

            # This assert supposedly prevents infinite loops,
            # but some customers actually have a lot of transactions.
            assert total_transactions < 50000, 'the site may be doing an infinite loop'

    @need_login
    def iter_investments(self, account):
        if account.type == Account.TYPE_LIFE_INSURANCE:
            for invest in account._investments:
                yield invest

        elif account.type in (Account.TYPE_PEA, Account.TYPE_MARKET):
            if 'Portefeuille Titres' in account.label:
                if account._is_in_linebourse:
                    if account._univers != self.current_univers:
                        self.move_to_universe(account._univers)
                    self.linebourse.location(
                        self.linebourse_urls[account._univers],
                        data={'SJRToken': self.linebourse_tokens[account._univers]}
                    )
                    self.linebourse.session.headers['X-XSRF-TOKEN'] = self.linebourse.session.cookies.get('XSRF-TOKEN')
                    for investment in self.linebourse.iter_investments(account.id.strip('0').split('.')[0]):
                        yield investment
                else:
                    raise NotImplementedError()
            else:
                # Compte espèces
                yield create_french_liquidity(account.balance)

        else:
            raise NotImplementedError()

    @need_login
    def iter_market_orders(self, account):
        if account.type not in (Account.TYPE_MARKET, Account.TYPE_PEA):
            return

        if 'Portefeuille Titres' in account.label:
            if account._is_in_linebourse:
                if account._univers != self.current_univers:
                    self.move_to_universe(account._univers)
                self.linebourse.location(
                    self.linebourse_urls[account._univers],
                    data={'SJRToken': self.linebourse_tokens[account._univers]}
                )
                self.linebourse.session.headers['X-XSRF-TOKEN'] = self.linebourse.session.cookies.get('XSRF-TOKEN')
                for order in self.linebourse.iter_market_orders(account.id.strip('0').split('.')[0]):
                    yield order

    @need_login
    def get_profile(self):
        self.get_universes()

        self.profile.go()
        profile = self.page.get_profile()

        try:
            self.emails.go()
        except ClientError as e:
            if e.response.status_code == 403:
                msg = e.response.json().get('erreur', {}).get('libelle', '')
                if msg == "Cette fonctionnalité n'est pas disponible avec votre compte.":
                    # We cannot get the mails, return the profile now.
                    return profile
            raise

        self.page.set_email(profile=profile)

        return profile

    @need_login
    def fill_account(self, account, fields):
        if account.type == Account.TYPE_CHECKING and 'iban' in fields:
            self.iban.go(number=account._number)
            self.page.set_iban(account=account)

    @need_login
    def iter_transfer_recipients(self, account):
        self.update_headers()

        try:
            self.emitters_list.go(json={
                'typeVirement': 'C',
            })
        except ClientError as e:
            if e.response.status_code == 403:
                msg = e.response.json().get('erreur', {}).get('libelle', '')
                if msg == "Cette fonctionnalité n'est pas disponible avec votre compte.":
                    # Means the account cannot emit transfers
                    return
            raise

        if not self.page.can_account_emit_transfer(account.id):
            return

        self.update_headers()
        account_id = account.id.split('.')[0]
        self.recipient_list.go(json={
            'numeroCompteDebiteur': account_id,
            'typeVirement': 'C',
        })

        for obj in self.page.iter_external_recipients():
            yield obj

        for obj in self.page.iter_internal_recipients():
            if obj.id != account.id:
                yield obj

    def do_strong_authent_recipient(self, recipient):
        self.list_authent.go(context=self.context['contextAppli'])
        self.auth_method = self.page.get_handled_auth_methods()

        if not self.auth_method:
            raise AuthMethodNotImplemented()

        self.need_reload_state = self.auth_method != 'password'

        if self.auth_method == 'password':
            return self.validate_strong_authent_recipient(self.password)
        elif self.auth_method == 'otp':
            raise AddRecipientStep(
                recipient,
                Value(
                    'otp',
                    label="Veuillez générez un e-Code sur votre application BRED puis saisir cet e-Code ici",
                ),
            )
        elif self.auth_method == 'notification':
            self.update_headers()
            self.init_authent.go(json={
                'context': self.context['contextAppli'],
                'type_auth': 'NOTIFICATION',
                'type_phone': 'P',
            })
            self.authent_id = self.page.get_authent_id()
            raise AppValidation(
                resource=recipient,
                message='Veuillez valider la notification sur votre application mobile BRED',
            )
        elif self.auth_method == 'sms':
            self.update_headers()
            self.send_sms.go(json={
                'contextAppli': self.context['contextAppli'],
                'context': self.context['context'],
            })
            raise AddRecipientStep(
                recipient,
                Value('code', label='Veuillez saisir le code reçu par SMS'),
            )

    def validate_strong_authent_recipient(self, auth_value):
        self.update_headers()
        self.check_otp.go(
            auth_method=self.auth_method,
            json={
                'contextAppli': self.context['contextAppli'],
                'context': self.context['context'],
                'otp': auth_value,
            },
        )

        error = self.page.get_error()
        if error:
            raise AddRecipientBankError(message=error)

    def find_recipient_transfer_limit(self, recipient):
        # The goal of this is to find the maximum allowed, by Bred, limit for transfers
        # on new recipient. For this we do a request with an absurdly high limit , and the
        # error message will tell us what is that maximum allowed limit by the bank.
        json_data = {
            'nom': recipient.label,
            'iban': recipient.iban,
            'numCompte': '',
            'plafond': '9999999999999999999999999',
            'typeDemande': 'A',
        }
        self.update_headers()
        self.add_recipient.go(json=json_data)

        max_limit = self.page.get_transfer_limit()
        assert max_limit, 'Could not find transfer max limit'
        return int(max_limit)

    def new_recipient(self, recipient, **params):
        if 'otp' in params:
            self.validate_strong_authent_recipient(params['otp'])
        elif 'code' in params:
            self.validate_strong_authent_recipient(params['code'])
        elif 'resume' in params:
            self.handle_polling(enrol=False)

        return self.init_new_recipient(recipient, **params)

    @need_login
    def init_new_recipient(self, recipient, **params):
        if not self.recipient_transfer_limit:
            self.recipient_transfer_limit = self.find_recipient_transfer_limit(recipient)

        json_data = {
            'nom': recipient.label,
            'iban': recipient.iban,
            'numCompte': '',
            'plafond': self.recipient_transfer_limit,
            'typeDemande': 'A',
        }
        try:
            self.update_headers()
            self.add_recipient.go(json=json_data)
        except ClientError as e:
            if e.response.status_code != 449:
                # Status code 449 means we need to do strong authentication
                raise

            self.context = e.response.json()['content']
            self.do_strong_authent_recipient(recipient)

            # Password authentication do not raise error, so we need
            # to re-execute the request here.
            if self.auth_method == 'password':
                self.update_headers()
                self.add_recipient.go(json=json_data)

        error = self.page.get_error()
        if error:
            raise AddRecipientBankError(message=error)

        return recipient

    @need_login
    def init_transfer(self, transfer, account, recipient, **params):
        account_id = account.id.split('.')[0]
        poste = account.id.split('.')[1]

        amount = transfer.amount.quantize(Decimal(10) ** -2)
        if not amount % 1:
            # Number is an integer without floating numbers.
            # We need to not display the floating points in the request
            # if the number is an integer or the request will not work.
            amount = int(amount)

        json_data = {
            'compteDebite': account_id,
            'posteDebite': poste,
            'deviseDebite': account.currency,
            'deviseCredite': recipient.currency,
            'dateEcheance': transfer.exec_date.strftime('%d/%m/%Y'),
            'montant': str(amount),
            'motif': transfer.label,
            'virementListe': True,
            'plafondBeneficiaire': '',
            'nomBeneficiaire': recipient.label,
            'checkBeneficiaire': False,
            'instantPayment': False,
        }

        if recipient.category == "Interne":
            recipient_id_split = recipient.id.split('.')
            json_data['compteCredite'] = recipient_id_split[0]
            json_data['posteCredite'] = recipient_id_split[1]
        else:
            json_data['iban'] = recipient.iban

        self.update_headers()
        self.create_transfer.go(json=json_data)

        error = self.page.get_error()
        if error:
            raise TransferBankError(message=error)

        transfer.amount = self.page.get_transfer_amount()
        transfer.currency = self.page.get_transfer_currency()

        # The same data is needed to validate the transfer.
        transfer._json_data = json_data
        return transfer

    @need_login
    def execute_transfer(self, transfer, **params):
        self.update_headers()
        # This sends an email to the user to tell him that a transfer
        # has been created.
        self.validate_transfer.go(json=transfer._json_data)

        error = self.page.get_error()
        if error:
            raise TransferBankError(message=error)

        return transfer
