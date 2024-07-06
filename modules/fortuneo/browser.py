# -*- coding: utf-8 -*-

# Copyright(C) 2012 Gilles-Alexandre Quenot
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

import time
import json
import re
from datetime import datetime, timedelta

from woob.browser import URL, need_login
from woob.browser.exceptions import ClientError
from woob.browser.filters.standard import RegexpError
from woob.browser.mfa import TwoFactorBrowser
from woob.exceptions import (
    AuthMethodNotImplemented, BrowserQuestion, BrowserIncorrectPassword, ActionNeeded,
    BrowserUnavailable,
)
from woob.capabilities.bank import (
    Account, AddRecipientStep, Recipient, Loan, Transaction,
    AddRecipientBankError, TransferStep,
)
from woob.tools.capabilities.bank.investments import create_french_liquidity
from woob.tools.capabilities.bank.transactions import sorted_transactions
from woob.tools.capabilities.bank.bank_transfer import sorted_transfers
from woob.tools.value import Value

from .pages.login import LoginPage, TwoFaPage, UnavailablePage
from .pages.accounts_list import (
    AccountsList, AccountHistoryPage, CardHistoryPage,
    InvestmentHistoryPage, PeaHistoryPage, LoanPage,
    ProfilePage, ProfilePageCSV, SecurityPage, FalseActionPage,
    InformationsPage, ActionNeededPage, InvestmentApiPage,
)
from .pages.transfer import (
    RegisterTransferPage, ValidateTransferPage, ConfirmTransferPage, RecipientsPage, ConfirmRecipientPage,
    TransferListPage, OTPSMSPage,
)

__all__ = ['FortuneoBrowser']


class FortuneoBrowser(TwoFactorBrowser):
    HAS_CREDENTIALS_ONLY = True
    BASEURL = 'https://mabanque.fortuneo.fr'
    STATE_DURATION = 5

    login_page = URL(r'.*identification\.jsp.*', LoginPage)
    twofa_page = URL(
        r'.*/prive/mes-comptes/synthese-mes-comptes.jsp',
        r'.*/prive/listes-personnelles.jsp',
        r'.*/prive/obtenir-otp-connexion.jsp',
        r'.*/prive/valider-otp-connexion.jsp',
        TwoFaPage
    )
    accounts_page = URL(
        r'/fr/prive/default.jsp\?ANav=1',
        r'.*prive/default\.jsp.*',
        r'.*/prive/mes-comptes/synthese-mes-comptes\.jsp',
        AccountsList
    )
    transfer_history = URL(
        r'.*/prive/mes-comptes/.*/realiser-operations/operations-en-cours/initialiser-operations-en-cours.jsp\?ca=(?P<ca>\.*)',
        TransferListPage
    )
    account_history = URL(
        r'.*/prive/mes-comptes/livret/consulter-situation/consulter-solde\.jsp.*',
        r'.*/prive/mes-comptes/compte-courant/consulter-situation/consulter-solde\.jsp.*',
        r'.*/prive/mes-comptes/compte-especes.*',
        AccountHistoryPage
    )
    card_history = URL(
        r'.*/prive/mes-comptes/compte-courant/carte-bancaire/encours-debit-differe\.jsp.*',
        CardHistoryPage
    )
    pea_history = URL(
        r'.*/prive/mes-comptes/pea/.*',
        r'.*/prive/mes-comptes/compte-titres-pea/.*',
        r'.*/prive/mes-comptes/ppe/.*',
        PeaHistoryPage
    )
    invest_history = URL(r'.*/prive/mes-comptes/assurance-vie/.*', InvestmentHistoryPage)
    ajax_sync_call = URL(r'/AsynchAjax\?key0=(?P<params_hash>[^&]*)&div0=(?P<action>[^&]*)&time=450')
    loan_contract = URL(
        r'/fr/prive/mes-comptes/credit-immo/contrat-credit-immo/contrat-pret-immobilier.jsp.*',
        LoanPage
    )
    unavailable = URL(r'/customError/indispo.html', UnavailablePage)
    security_page = URL(r'/fr/prive/identification-carte-securite-forte.jsp.*', SecurityPage)
    informations_page = URL(r'/fr/prive/accueil-informations-client-partiel.jsp', InformationsPage)

    # transfer
    recipients = URL(
        r'/fr/prive/mes-comptes/compte-courant/realiser-operations/gerer-comptes-externes/consulter-comptes-externes.jsp',
        r'/fr/prive/verifier-compte-externe.jsp',
        r'fr/prive/mes-comptes/compte-courant/.*/gestion-comptes-externes.jsp',
        RecipientsPage)
    confirm_recipient = URL(
        r'/fr/prive/mes-comptes/compte-courant/.*/confirmer-ajout-compte-externe.jsp',
        ConfirmRecipientPage)
    register_transfer = URL(
        r'/fr/prive/mes-comptes/compte-courant/realiser-operations/saisie-virement.jsp\?ca=(?P<ca>)',
        RegisterTransferPage)
    validate_transfer = URL(
        r'/fr/prive/mes-comptes/compte-courant/.*/verifier-saisie-virement.jsp',
        ValidateTransferPage)
    confirm_transfer = URL(
        r'fr/prive/mes-comptes/compte-courant/.*/init-confirmer-saisie-virement.jsp',
        r'/fr/prive/mes-comptes/compte-courant/.*/confirmer-saisie-virement.jsp',
        ConfirmTransferPage)
    otp_sms_page = URL(
        r'/fr/prive/appel-securite-forte-otp-bankone.jsp',
        OTPSMSPage,
    )
    false_action_page = URL(r'fr/prive/mes-comptes/synthese-globale/synthese-mes-comptes.jsp', FalseActionPage)
    profile = URL(r'/fr/prive/informations-client.jsp', ProfilePage)
    profile_csv = URL(r'/PdfStruts\?*', ProfilePageCSV)

    # Fortuneo's API routes
    set_cookies_api = URL(r'/valorisation-assurance-vie/api/security/oauth/sso-module\?response_type=token')
    api_investments_life_insurance = URL(
        r'/valorisation-assurance-vie/api/life-insurance/accounts/(?P<id>.+)',
        InvestmentApiPage
    )

    need_reload_state = None

    __states__ = ['need_reload_state', 'add_recipient_form', 'sms_form', 'execute_transfer_form']

    def __init__(self, config, *args, **kwargs):
        super(FortuneoBrowser, self).__init__(config, *args, **kwargs)
        self.investments = {}
        self.action_needed_processed = False
        self.add_recipient_form = None
        self.sms_form = None

        self.AUTHENTICATION_METHODS = {
            'code': self.handle_sms,
        }

    def init_login(self):
        self.first_login_step()

        if self.twofa_page.is_here():
            information = self.page.get_warning_message()
            if 'Cette opération sensible doit être validée par un code sécurité' in information:
                # The user must contact its bank to add phone information for future 2fa
                # Here we can continue the login, nevertheless in this case fortuneo always
                # gives 2fa presence (X-Arkea-sca header) until the user doesn't give phone
                # information and validate one 90d 2fa.
                raise ActionNeeded(information)

            # Need to convert Form object into dict for storage
            self.sms_form = dict(self.page.get_sms_form())
            self.need_reload_state = True
            raise BrowserQuestion(Value('code', label='Entrez le code reçu par SMS'))

        self.last_login_step()

    def first_login_step(self):
        if not self.login_page.is_here():
            self.location('/fr/identification.jsp')

        try:
            self.page.login(self.username, self.password)
        except ClientError as e:
            # Invalid credentials responses are only 401
            # and there's no location header attached to it.
            # In case of a 401 error, in order to get the proper
            # error message, the website just reloads
            # '/fr/identification.jsp', no redirections are used here.
            if e.response.status_code == 401:
                self.location('/fr/identification.jsp')
            else:
                raise
        if self.login_page.is_here():
            login_error = self.page.get_login_error()
            self.handle_login_error(login_error)

        # By default we are redirected to the '/fr/prive/default.jsp\?ANav=1' accounts_page URL.
        # It will bear a basic list of accounts, but 2FA will still be triggered
        # when requesting accounts details in iter_accounts()
        # So we force go to this other URL to trigger it now if it is needed.
        self.location('/fr/prive/mes-comptes/synthese-mes-comptes.jsp')
        if not self.twofa_page.is_here():
            self.check_and_handle_action_needed()

    def handle_login_error(self, error):
        wrongpass_regex = re.compile(
            'anomalie est survenue'
            + '|mot de passe et/ou votre identifiant est erroné'
            + '|mot de passe et/ou identifiant est erroné'
            + '|identifiant n\'est plus actif'
            + '|accès est désormais bloqué'  # user must submit new creds or access will still be blocked on next try
        )

        if wrongpass_regex.search(error):
            raise BrowserIncorrectPassword(error)

        browserunavailable_regex = re.compile(
            'Nous ne pouvons donner suite à votre demande'
            + '|Certificat invalide'
        )

        if browserunavailable_regex.search(error):
            raise BrowserUnavailable()

        raise AssertionError('Unknown error at login: %s' % error)

    def handle_sms(self):
        if not self.sms_form:
            # An action needed can happen during the handle_sms,
            # but self.sms_form will have been re-initiated to None while the user resolve it,
            # and the OTP will already been submitted and accepted by the server
            # So, to avoid running handle_sms a second time, we check if self.sms_form is present;
            # when not, we fall back to init_login, where the SCA won't be triggered.
            self.init_login()
            return

        self.sms_form['otp'] = self.code
        self.sms_form['typeOperationSensible'] = 'AUTHENTIFICATION_FORTE_CONNEXION'
        self.location('/fr/prive/valider-otp-connexion.jsp', data=self.sms_form)

        self.sms_form = None
        self.page.check_otp_error_message()

        self.location('/fr/prive/mes-comptes/synthese-mes-comptes.jsp')
        self.check_and_handle_action_needed()
        self.last_login_step()

    def last_login_step(self):
        self.location('/fr/prive/default.jsp?ANav=1')
        if self.accounts_page.is_here() and self.page.need_sms():
            raise AuthMethodNotImplemented('Authentification with sms is not supported')

    def load_state(self, state):
        # reload state only for new recipient and 2fa features
        if state.get('need_reload_state') or state.get('sms_form'):
            # don't use locate browser for add recipient step and 2fa validation
            state.pop('url', None)
            super(FortuneoBrowser, self).load_state(state)

    def process_skippable_message(self):
        global_error_message = self.page.get_global_error_message()
        if global_error_message:
            if 'Et si vous faisiez de Fortuneo votre banque principale' in global_error_message:
                self.location('/ReloadContext', data={'action': 4})
                return True
            elif 'vous allez recevoir un code' in global_error_message:
                raise AuthMethodNotImplemented(
                    "Nous ne sommes pas en mesure de gérer l'option “Activation du code sécurité à la connexion”."
                    + " Veuillez la désactiver dans l'onglet “Configuration du site” pour continuer."
                )
            else:
                raise ActionNeeded(global_error_message)

        local_error_message = self.page.get_local_error_message()
        if local_error_message:
            raise BrowserUnavailable(local_error_message)

    def check_and_handle_action_needed(self):
        # Note: if you want to debug process_action_needed() here,
        # you must first set self.action_needed_processed to False
        # otherwise it might not enter the "if" loop here below.
        if not self.action_needed_processed:
            self.process_action_needed()

        assert self.accounts_page.is_here()

        if self.false_action_page.is_here() or self.page.has_skippable_action_needed():
            # A false action needed is present, it's a choice to make Fortuneo your main bank.
            # To avoid it, we need to first detect it on the account_page
            # Then make a post request to mimic the click on choice 'later'
            # And to finish we must to reload the page with a POST to get the accounts
            # before going on the accounts_page, which will have the data.
            self.location(self.absurl('ReloadContext?action=1&', base=True), method='POST')
            self.accounts_page.go()

    def check_and_raise_action_needed(self):
        message = self.page.get_action_needed_message()
        if message:
            raise ActionNeeded(message)

    @need_login
    def iter_accounts(self):
        self.accounts_page.go()
        self.check_and_handle_action_needed()

        accounts = self.page.iter_accounts()

        for account in accounts:
            if account._investment_link:
                self.location(account._investment_link)

                if self.process_skippable_message():
                    self.location(account._investment_link)

                self.check_and_raise_action_needed()

                if account.type == Account.TYPE_LIFE_INSURANCE:
                    account_api_id = self.page.get_account_api_id()
                    # Get cookies to access Fortuneo's API
                    self.set_cookies_api.go(data='')
                    self.api_investments_life_insurance.go(id=account_api_id)
                    self.page.fill_account(obj=account)
                else:
                    self.page.fill_account(obj=account)
            else:
                self.location(account._history_link)

                # Sometimes the website displays a message about preventing scams.
                if self.page.send_info_form():
                    self.location(account._history_link)

                if self.process_skippable_message():
                    self.location(account._history_link)

                self.check_and_raise_action_needed()

                if self.loan_contract.is_here():
                    loan = Loan.from_dict(account.to_dict())
                    loan._ca = account._ca
                    loan._card_links = account._card_links
                    loan._investment_link = account._investment_link
                    loan._history_link = account._history_link
                    account = loan
                    self.page.fill_account(obj=account)
                else:
                    self.page.fill_account(obj=account)

                    if account.type == account.TYPE_CHECKING:
                        for _ in range(3):
                            self.location('/fr/prive/mes-comptes/synthese-mes-comptes.jsp')
                            if not self.page.is_loading():
                                break
                            time.sleep(1)
                        # TPP can match checking accounts with this id
                        self.page.fill_tpp_account_id(obj=account)
                        if not account._tpp_id:
                            self.register_transfer.go(ca=account._ca)
                            self.page.fill_tpp_account_id(obj=account)

                            if not account._tpp_id:
                                self.logger.warning(
                                    'Could not find the tpp_id of account %s',
                                    account.id
                                )

            yield account

    @need_login
    def iter_investments(self, account):
        if not account._investment_link:
            return

        self.location(account._investment_link)

        if account.type == Account.TYPE_LIFE_INSURANCE:
            account_api_id = self.page.get_account_api_id()
            self.api_investments_life_insurance.go(id=account_api_id)

        for inv in self.page.iter_investments():
            yield inv

        # As we already return Compte espèce, we must not return Compte Titres' liquidity
        # Otherwise it would be a duplicate of the same data
        if account.type != Account.TYPE_MARKET and self.pea_history.is_here():
            liquidity = self.page.get_liquidity()
            if liquidity:
                yield create_french_liquidity(liquidity)

    @need_login
    def iter_market_orders(self, account):
        if account.type not in (Account.TYPE_MARKET, Account.TYPE_PEA) or not account._market_orders_link:
            return

        self.location(account._market_orders_link)

        # Market orders are loaded with an AJAX call
        # It loads the market orders table and the form to choose a range of dates
        for _ in range(3):
            self.location(account._market_orders_link)
            if self.page.are_market_orders_loaded():
                break
            self.logger.debug('Sleeping for a few seconds so market orders can load...')
            time.sleep(3)

        form = self.page.get_date_range_form()
        # Once we submit the form with the date range,
        # we need to reload the page until they are loaded
        for _ in range(3):
            form.submit()
            if self.page.are_market_orders_loaded():
                break
            self.logger.debug('Sleeping for a few seconds so market orders can load...')
            time.sleep(3)

        for market_order in self.page.iter_market_orders():
            if market_order._details_link:
                self.location(market_order._details_link)
                self.page.fill_market_order(obj=market_order)
            yield market_order

    @need_login
    def iter_history(self, account):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_MORTGAGE,):
            return []

        self.location(account._history_link)

        if self.page.select_period():
            raw_transactions = list(self.page.iter_history())

            # We replace transactions with their subtransactions if they have any
            transactions = []
            for tr in raw_transactions:
                # There is no difference between card transaction and deferred card transaction
                # on the history.
                if tr.type == Transaction.TYPE_CARD:
                    tr.bdate = tr.rdate

                if tr._details_link:
                    self.location(tr._details_link)
                    detailed_transactions = list(self.page.iter_detail_history())
                    if detailed_transactions:
                        transactions.extend(detailed_transactions)
                    else:
                        transactions.append(tr)
                else:
                    transactions.append(tr)

            return sorted_transactions(transactions)

        return []

    @need_login
    def iter_coming(self, account):
        for cb_link in account._card_links:
            for _ in range(3):
                self.location(cb_link)
                if not self.page.is_loading():
                    break
                self.logger.debug('Sleeping for a second before we retry opening CB link...')
                time.sleep(1)

            for tr in sorted_transactions(self.page.iter_coming()):
                yield tr

    def process_action_needed(self):
        # we have to go in an iframe to know if there are CGUs
        url = self.page.get_iframe_url()
        if url:
            self.location(self.absurl(url, base=True))  # beware, the landing page might vary according to the referer page. So far I didn't figure out how the landing page is chosen.

            if self.security_page.is_here():
                # Some connections require reinforced security and we cannot bypass the OTP in order
                # to get to the account information. Users have to provide a phone number in order to
                # validate an OTP, so we must raise an ActionNeeded with the appropriate message.
                raise ActionNeeded(
                    "Cette opération sensible doit être validée par un code sécurité envoyé par SMS ou serveur vocal."
                    + " Veuillez contacter le Service Clients pour renseigner vos coordonnées téléphoniques."
                )

            # if there are skippable CGUs, skip them
            if isinstance(self.page, ActionNeededPage):
                if self.page.has_skippable_action_needed():
                    # Look for the request in the event listener registered to the button
                    # can be harcoded, no variable part. It is a POST request without data.
                    self.location(self.absurl('ReloadContext?action=1&', base=True), method='POST')
                elif self.page.get_action_needed_message():
                    raise ActionNeeded(self.page.get_action_needed_message())

            self.accounts_page.go()  # go back to the accounts page whenever there was an iframe or not

        self.action_needed_processed = True

    @need_login
    def iter_recipients(self, origin_account):
        self.register_transfer.go(ca=origin_account._ca)
        if self.page.is_account_transferable(origin_account):
            for internal_recipient in self.page.iter_internal_recipients(origin_account_id=origin_account.id):
                yield internal_recipient

            self.recipients.go()
            for external_recipients in self.page.iter_external_recipients():
                yield external_recipients

    def copy_recipient(self, recipient):
        rcpt = Recipient()
        rcpt.iban = recipient.iban
        rcpt.id = recipient.iban
        rcpt.label = recipient.label
        rcpt.category = recipient.category
        rcpt.enabled_at = datetime.now().replace(microsecond=0) + timedelta(days=1)
        rcpt.currency = u'EUR'
        return rcpt

    def new_recipient(self, recipient, **params):
        if 'code' in params:
            # to drop and use self.add_recipient_form instead in send_code()
            recipient_form = json.loads(self.add_recipient_form)
            self.send_code(recipient_form, params['code'])
            if self.page.rcpt_after_sms():
                self.need_reload_state = None
                return self.copy_recipient(recipient)
            elif self.page.is_code_expired():
                self.need_reload_state = True
                raise AddRecipientStep(recipient, Value('code', label='Le code sécurité est expiré. Veuillez saisir le nouveau code reçu qui sera valable 5 minutes.'))
            else:
                error = self.page.get_error()
                if 'Le code saisi est incorrect' in error:
                    raise AddRecipientBankError(message=error)
                raise AssertionError(error)
        return self.new_recipient_before_otp(recipient, **params)

    @need_login
    def new_recipient_before_otp(self, recipient, **params):
        self.recipients.go()

        # Sometimes the website displays a message about preventing scams, there is a form
        # that need to be sent to continue adding the recipient.
        # If the form exists and has been validated, we need to go on the recipients page again
        # because the form redirects us on another page.
        if self.page.send_info_form():
            self.recipients.go()

        # Skip useless messages, same way it is done in iter_accounts.
        if self.process_skippable_message():
            self.recipients.go()

        self.page.check_external_iban_form(recipient)
        self.page.check_recipient_iban()

        # fill form
        self.page.fill_recipient_form(recipient)

        error = self.page.get_error()
        if error:
            raise AddRecipientBankError(message=error)

        rcpt = self.page.get_new_recipient(recipient)

        # get first part of confirm form
        send_code_form = self.page.get_send_code_form()

        if 'fallbackSMS' in send_code_form:
            # Means we have an app validation, but we want to validate with sms
            # This send sms to user
            send_code_form.submit()
            send_code_form = self.page.get_send_code_form()
        else:
            self.logger.info('Old sms sending method is still used for new recipients')
            data = {
                'appelAjax': 'true',
                'domicileUpdated': 'false',
                'numeroSelectionne.value': '',
                'portableUpdated': 'false',
                'proUpdated': 'false',
                'typeOperationSensible': 'AJOUT_BENEFICIAIRE',
            }
            # this send sms to user
            self.otp_sms_page.go(data=data)
            send_code_form.update(self.page.get_send_code_form())

        # save form value and url for statesmixin
        self.add_recipient_form = dict(send_code_form)
        self.add_recipient_form.update({'url': send_code_form.url})

        # storage can't handle dict with '.' in key
        # to drop when dict with '.' in key is handled
        self.add_recipient_form = json.dumps(self.add_recipient_form)

        self.need_reload_state = True

        msg = 'Veuillez saisir le code reçu par sms'
        try:
            phone_number = self.page.get_phone_number()
            msg += ' au %s.' % phone_number
        except RegexpError:
            msg += '.'
        raise AddRecipientStep(rcpt, Value('code', label=msg))

    def send_code(self, form_data, code):
        form_url = form_data['url']
        form_data['otp'] = code
        form_data.pop('url')
        self.location(self.absurl(form_url, base=True), data=form_data)

    @need_login
    def init_transfer(self, account, recipient, amount, label, exec_date):
        self.register_transfer.go(ca=account._ca)
        used_recipient_id = self.page.fill_transfer_form_and_get_used_recipient_id(
            account, recipient, amount, label, exec_date,
        )
        return self.page.handle_response(account, recipient, amount, label, exec_date, used_recipient_id)

    @need_login
    def execute_transfer(self, transfer, **params):
        # sca final step
        if 'code' in params:
            self.validate_transfer_code(transfer, params['code'])
            self.page.confirm_transfer()
            return self.page.transfer_confirmation(transfer)

        # steps before sca
        self.page.validate_transfer()

        # get first part of confirm form
        send_code_form = self.page.get_send_code_form()
        if send_code_form:
            self.execute_transfer_sca(transfer, send_code_form)

        # SCA is not systematic
        self.page.confirm_transfer()
        return self.page.transfer_confirmation(transfer)

    def execute_transfer_sca(self, transfer, send_code_form):
        """Prompt the user for a SCA in order to execute a transfer

        Will call the Form with specific values that will trigger an SCA for a transfer.
        The resulting form will be saved in the browser state in order to be called after
        the module reloading, when the SMS code of the SCA has been given.

        @param transfer, the transfer to execute (will be returned in the TransferStep)
        @param send_code_form, The validation form obtained on the transfer execution page
        """
        data = {
            'appelAjax': 'true',
            'domicileUpdated': 'false',
            'numeroSelectionne.value': '',
            'portableUpdated': 'false',
            'proUpdated': 'false',
            'typeOperationSensible': 'VIREMENT',
        }
        # this send sms to user
        self.otp_sms_page.go(data=data)
        send_code_form.update(self.page.get_send_code_form())

        # save form value and url for statesmixin
        self.execute_transfer_form = dict(send_code_form)
        self.execute_transfer_form.update({'url': send_code_form.url})

        self.need_reload_state = True

        msg = 'Veuillez saisir le code reçu par sms'
        try:
            phone_number = self.page.get_phone_number()
            msg += ' au %s.' % phone_number
        except RegexpError:
            msg += '.'
        raise TransferStep(transfer, Value('code', label=msg))

    def validate_transfer_code(self, transfer, code):
        self.send_code(self.execute_transfer_form, code)
        if self.page.is_code_expired():
            self.need_reload_state = True
            label = 'Le code sécurité est expiré. Veuillez saisir le nouveau code reçu qui sera valable 5 minutes.'
            raise TransferStep(transfer, Value('code', label=label))
        error = self.page.get_error()
        if error:
            if 'Le code saisi est incorrect' in error:
                raise TransferStep(transfer, Value('code', label=error))
            raise AssertionError(error)
        # No exception if no error on this page

    @need_login
    def get_profile(self):
        self.profile.go()
        csv_link = self.page.get_csv_link()
        if csv_link:
            self.location(csv_link)
            return self.page.get_profile()
        # The persons name is in a menu not returned in the ProfilePage, so
        # we have to go back to the AccountsPage (which is the main page for the website)
        # to get the info
        person = self.page.get_profile()
        self.accounts_page.go()
        self.page.fill_person_name(obj=person)
        return person

    @need_login
    def iter_emitters(self):
        self.register_transfer.go(ca='')
        return self.page.iter_emitters()

    @need_login
    def iter_transfers(self, account):
        transfers_list = []
        if account is None:
            self.accounts_page.stay_or_go()
            history_links = self.page.iter_transfer_history_links()
        elif account._transfers_link:
            history_links = (account._transfers_link,)
        else:
            # iteration requested on an account unable to do transfers
            return []

        for history_link in history_links:
            self.location(history_link)
            for transfer in self.page.iter_transfers():
                transfers_list.append(transfer)
        return sorted_transfers(transfers_list)
