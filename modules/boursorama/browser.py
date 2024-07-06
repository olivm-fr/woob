# Copyright(C) 2016       Baptiste Delpey
# Copyright(C) 2016-2022  Powens
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

import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import requests
from dateutil.relativedelta import relativedelta
from requests.exceptions import ReadTimeout

from woob.browser.browsers import need_login
from woob.browser.exceptions import ClientError, HTTPNotFound, LoggedOut, ServerError
from woob.browser.filters.standard import Regexp
from woob.browser.mfa import TwoFactorBrowser
from woob.browser.pages import FormNotFound
from woob.browser.retry import RetryLoginBrowser, login_method, retry_on_logout
from woob.browser.url import URL
from woob.capabilities.bank import (
    Account, AccountNotFound, AccountOwnership, AddRecipientBankError,
    AddRecipientStep, AddRecipientTimeout, Emitter, NoAccountsException, Rate,
    RecipientNotFound, TransactionType, TransferBankError, TransferDateType,
    TransferError, TransferInvalidAmount, TransferInvalidEmitter, TransferInvalidLabel,
    TransferInvalidRecipient, TransferStep, TransferTimeout,
)
from woob.capabilities.base import NotAvailable, NotLoaded, empty, find_object, strict_find_object
from woob.capabilities.contact import Advisor
from woob.exceptions import (
    ActionNeeded, ActionType, AppValidation, AppValidationCancelled, AppValidationExpired,
    AuthMethodNotImplemented, BrowserHTTPNotFound, BrowserIncorrectPassword,
    BrowserPasswordExpired, BrowserUnavailable, BrowserUserBanned, OTPSentType, SentOTPQuestion,
)
from woob.tools.capabilities.bank.bank_transfer import sorted_transfers
from woob.tools.capabilities.bank.transactions import sorted_transactions
from woob.tools.date import now_as_utc
from woob.tools.decorators import retry
from woob.tools.misc import polling_loop
from woob.tools.value import Value

from .document_pages import BankIdentityPage, BankStatementsPage, PdfDocumentPage
from .pages import (
    AccountsErrorPage, AccountsPage, AddRecipientOtpSendPage, AddRecipientPage, AsvPage,
    AuthenticationPage, CalendarPage, CardCalendarPage, CardHistoryPage, CardInformationPage,
    CardRenewalPage, CardSumDetailPage, CATPage, CurrencyConvertPage, CurrencyListPage, ErrorPage,
    ExpertPage, HistoryPage, HomePage, IbanPage, IncidentPage, IncidentTradingPage, LoanPage,
    MarketPage, MinorPage, NewTransferConfirm, NewTransferEstimateFees, NewTransferSent,
    NewTransferUnexpectedStep, NewTransferWizard, NoAccountPage, OtpCheckPage, OtpPage, PasswordPage,
    PEPPage, PerPage, ProfilePage, RecipientsPage, SavingMarketPage, StatusPage, TncPage,
    TransferAccounts, TransferCharacteristics, TransferConfirm, TransferMainPage, TransferRecipients,
    TransferSent, VirtKeyboardPage,
)
from .transfer_pages import TransferInfoPage, TransferListPage

__all__ = ['BoursoramaBrowser']


class BoursoramaBrowser(RetryLoginBrowser, TwoFactorBrowser):
    BASEURL = 'https://clients.boursobank.com'
    TIMEOUT = 60.0
    HAS_CREDENTIALS_ONLY = True
    TWOFA_DURATION = 60 * 24 * 90

    home = URL('/$', HomePage)
    keyboard = URL(r'/connexion/clavier-virtuel\?_hinclude=1', VirtKeyboardPage)
    # following URL has to be declared early because there are two other URL with the same url
    # PdfDocumentPage has been declared with a is_here attribute to be differentiated to the 2 others
    # (the two other pages seem to be in csv format)
    pdf_document_page = URL(r'https://api.boursobank.com/services/api/files/download.phtml.*', PdfDocumentPage)
    status = URL(r'/aide/messages/dashboard\?showza=0&_hinclude=1', StatusPage)
    calendar = URL(r'/compte/cav/.*/calendrier', CalendarPage)
    card_calendar = URL(r'https://api.boursobank.com/services/api/files/download.phtml.*', CardCalendarPage)
    card_renewal = URL(r'/infos-profil/renouvellement-carte-bancaire', CardRenewalPage)
    incident_trading_page = URL(r'/infos-profil/incident-trading', IncidentTradingPage)
    error = URL(
        r'/connexion/compte-verrouille',
        r'/infos-profil',
        r'/connexion/compte-en-pause',
        r'/infos-profil/pedagogie-fraude/',
        ErrorPage,
    )
    login = URL(
        r'/connexion/saisie-mot-de-passe',
        # When getting logged out, we get redirected to
        # either /connexion/ or /connexion/?ubiquite=1 or /connexion/?ubiquite= or /connexion/?org=...
        r'/connexion/$',
        r'/connexion/(\?ubiquite=1?)?$',
        r'/connexion/(\?org=.*)?$',
        r'/connexion/\?expire=',
        r'/connexion/\?deconnexion=$',
        PasswordPage
    )
    otp_page = URL(
        r'https://api.boursobank.com/services/api/v1.7/_user_/_(?P<user_hash>.*)_/session/(?P<otp_challenge>(challenge|otp))/(?P<otp_operation>(check|start))(?P<otp_type>.*)/(?P<otp_number>.*)',
        OtpPage
    )
    minor = URL(r'/connexion/mineur', MinorPage)
    accounts = URL(r'/produits/mes-produits', AccountsPage)
    accounts_error = URL(r'/dashboard/comptes\?_hinclude=300000', AccountsErrorPage)
    no_account = URL(
        r'/dashboard/comptes\?_hinclude=300000',
        r'/dashboard/comptes-professionnels\?_hinclude=1',
        NoAccountPage
    )

    history = URL(r'/compte/(cav|epargne)/(?P<webid>.*)/mouvements.*', HistoryPage)
    card_transactions = URL(r'/compte/cav/(?P<webid>.*)/carte/.*', HistoryPage)
    deffered_card_history = URL(r'https://api.boursobank.com/services/api/files/download.phtml.*', CardHistoryPage)
    budget_transactions = URL(r'/budget/compte/(?P<webid>.*)/mouvements.*', HistoryPage)
    other_transactions = URL(r'/compte/cav/(?P<webid>.*)/mouvements.*', HistoryPage)
    saving_transactions = URL(r'/compte/epargne/csl/(?P<webid>.*)/mouvements.*', HistoryPage)
    card_summary_detail_transactions = URL(r'/contre-valeurs-operation/.*', CardSumDetailPage)
    saving_pep = URL(r'/compte/epargne/pep', PEPPage)
    saving_cat = URL(r'/compte/epargne/cat', CATPage)
    incident = URL(r'/compte/cav/(?P<webid>.*)/mes-incidents.*', IncidentPage)

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

    # transfer_recipients_page is the 'select a recipient' interface from the
    # transfer flow. It includes internal recipients, so it is used to list
    # available recipients; it doesn't always have the 'add recipient'
    # button.
    transfer_recipients_page = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements$',
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/2',
        TransferRecipients
    )

    # recipients_page is the 'manage recipients' interface, which only lists
    # external recipients but always has an 'add recipient' button if
    # accessible.
    recipients_page = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/comptes-externes/$',
        RecipientsPage,
    )

    transfer_characteristics = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/3',
        TransferCharacteristics
    )
    transfer_confirm = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/4',
        TransferConfirm
    )
    transfer_sent = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/nouveau/(?P<id>\w+)/5',
        TransferSent
    )
    # transfer_type should be one of : "immediat", "programme"
    new_transfer_wizard = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/(?P<transfer_type>immediat|programme)/nouveau/?$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/immediat/nouveau/(?P<id>\w+)/(?P<step>[1-6])$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/programme/nouveau/(?P<id>\w+)/(?P<step>[1-7])$',
        NewTransferWizard
    )
    new_transfer_estimate_fees = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/immediat/nouveau/(?P<id>\w+)/7$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/programme/nouveau/(?P<id>\w+)/8$',
        NewTransferEstimateFees
    )
    new_transfer_confirm = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/immediat/nouveau/(?P<id>\w+)/[78]$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/programme/nouveau/(?P<id>\w+)/[89]$',
        NewTransferConfirm
    )
    new_transfer_unexpected_step = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/immediat/nouveau/(?P<id>\w+)/7$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/programme/nouveau/(?P<id>\w+)/8$',
        NewTransferUnexpectedStep
    )
    new_transfer_sent = URL(
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/immediat/nouveau/(?P<id>\w+)/9$',
        r'/compte/(?P<acc_type>[^/]+)/(?P<webid>\w+)/virements/programme/nouveau/(?P<id>\w+)/10$',
        NewTransferSent
    )
    rcpt_page = URL(
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/comptes-externes/nouveau/(?P<id>\w+)/\d',
        r'/compte/(?P<type>[^/]+)/(?P<webid>\w+)/virements/comptes-externes/nouveau$',
        AddRecipientPage
    )
    rcpt_send_otp_page = URL(
        r'https://api.boursobank.com/services/api/v\d+\.\d+/_user_/_\w+_/session/(otp|challenge)/start(?P<otp_type>\w+)/\d+',
        AddRecipientOtpSendPage,
    )
    rcpt_check_otp_page = URL(
        r'https://api.boursobank.com/services/api/v\d+\.\d+/_user_/_\w+_/session/(otp|challenge)/check(?P<otp_type>\w+)/\d+',
        OtpCheckPage,
    )

    asv = URL(r'/compte/assurance-vie/.*', AsvPage)
    per = URL(r'/compte/per/.*', PerPage)
    saving_history = URL(
        r'/compte/cefp/.*/(positions|mouvements)',
        r'/compte/.*ord/.*/mouvements',
        r'/compte/pea/.*/mouvements',
        r'/compte/0%25pea/.*/mouvements',
        r'/compte/pea-pme/.*/mouvements',
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
    # At the moment we don't manage tnc ('titres non cotés') accounts, so if we are on the tnc page, we will ignore the account.
    tnc = URL(r'/compte/tnc/.*/investissements', TncPage)
    authentication = URL(r'/securisation$', AuthenticationPage)
    # We need this URL to make a post to validate the twofa
    authentication_validation = URL(r'/securisation/validation', AuthenticationPage)
    iban = URL(r'/compte/(?P<webid>.*)/rib', IbanPage)
    profile = URL(r'/mon-profil/', ProfilePage)
    profile_children = URL(r'/mon-profil/coordonnees/enfants', ProfilePage)

    expert = URL(r'/compte/derive/', ExpertPage)

    card_information = URL(
        r'/compte/cav/cb/informations/(?P<webid>.*)/(?P<key>.*)',
        r'/compte/cav/cb/(?P<webid>.*)?creditCardKey=(?P<key>.*)',
        CardInformationPage
    )

    currencylist = URL(r'https://www.boursorama.com/bourse/devises/parite/_detail-parite', CurrencyListPage)
    currencyconvert = URL(
        r'https://www.boursorama.com/bourse/devises/convertisseur-devises/convertir',
        CurrencyConvertPage
    )

    statements_page = URL(r'/documents/releves', BankStatementsPage)
    rib_page = URL(r'/documents/compte-bancaire', BankIdentityPage)

    __states__ = (
        'recipient_form', 'transfer_form', 'form_state',
        'user_hash', 'otp_number', 'otp_form_token',
        'twofa_config', 'otp_headers', 'twofa_count',
    )

    def __init__(self, config=None, *args, **kwargs):
        self.config = config
        self.cards_list = None
        self.deferred_card_calendar = None
        # Card calendar page not always present
        # set it to True on CardCalendarPage's on_load method (loaded after a redirection)
        self.card_calendar_loaded = False
        self.recipient_form = None
        self.transfer_form = None
        self.clear_twofa_params()
        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()

        self.AUTHENTICATION_METHODS = {
            'code': self.handle_sms,
            'email_code': self.handle_email,
            'resume': self.handle_polling,
        }

        super(BoursoramaBrowser, self).__init__(config, *args, **kwargs)

    def clear_twofa_params(self):
        self.otp_number = None
        self.user_hash = None
        self.otp_headers = None
        self.otp_form_token = None
        self.twofa_config = None
        self.form_state = None
        self.twofa_count = 0

    def locate_browser(self, state):
        try:
            self.location(state['url'])
        except (requests.exceptions.HTTPError, requests.exceptions.TooManyRedirects, LoggedOut):
            pass

    def load_state(self, state):
        # needed to continue the session while adding recipient with otp or 2FA validation
        # it keeps the form to continue to submit the otp
        if state.get('recipient_form') or state.get('transfer_form') or state.get('otp_number'):
            state.pop('url', None)

        super(BoursoramaBrowser, self).load_state(state)

    def trigger_twofa(self):
        # With bourso, the user could perform several consecutive twofa (e.g. SMS + email).
        # In the event of a problem, an AssertionError is raised if more than two
        # consecutive twofa are performed, to avoid infinite loops.
        self.twofa_count += 1
        if self.twofa_count > 2:
            self.twofa_count = 0
            raise AssertionError('more than two consecutive twofa')

        available_twofa_type = {
            'sms': SentOTPQuestion(
                'code',
                medium_type=OTPSentType.SMS,
                message='Entrez le code reçu par SMS',
            ),
            'email': SentOTPQuestion(
                'email_code',
                medium_type=OTPSentType.EMAIL,
                message='Entrez le code reçu par EMAIL',
            ),
            'webtoapp': AppValidation(
                message='Une notification à valider vous a été envoyée',
            ),
        }
        self.twofa_config = self.page.get_twofa_config()

        api_config = self.page.get_api_config()
        otp_token = f"Bearer {api_config['DEFAULT_API_BEARER']}"
        self.otp_headers = {
            'Authorization': otp_token,
            'x-referer-feature-id': '_._.sca',
        }
        self.otp_form_token = self.page.get_form_token()
        self.user_hash = api_config['USER_HASH']
        self.otp_number = self.page.get_otp_number()
        self.form_state = self.page.get_form_state()

        self.otp_page.open(
            user_hash=self.user_hash, otp_number=self.otp_number,
            otp_challenge=self.twofa_config['otp_challenge'], otp_operation='start',
            otp_type=self.twofa_config['otp_type'], headers=self.otp_headers, json={'formState': self.form_state},
        )

        raise available_twofa_type[self.twofa_config['otp_type']]

    def check_twofa_status(self, token=None):
        json = {'formState': self.form_state}

        if token:
            json['token'] = token

        self.otp_page.go(
            user_hash=self.user_hash, otp_number=self.otp_number,
            otp_challenge=self.twofa_config['otp_challenge'], otp_operation='check',
            otp_type=self.twofa_config['otp_type'], headers=self.otp_headers,
            json=json,
        )

    def validate_twofa(self):
        self.authentication_validation.go(data={'form[_token]': self.otp_form_token})
        if self.authentication.is_here():
            # Several consecutive twofa may be required (e.g. sms + email)
            self.trigger_twofa()

        self.clear_twofa_params()

    def handle_authentication(self):
        if self.authentication.is_here():
            confirmation_link = self.page.get_confirmation_link()
            if confirmation_link:
                self.location(confirmation_link)

            if self.page.has_skippable_2fa():
                # The 2FA can be done before the end of the 90d
                # We skip it
                return

            self.check_interactive()
            self.trigger_twofa()

    def handle_code_otp(self, token):
        try:
            self.check_twofa_status(token=token)
        except ServerError as e:
            error = e.response.json().get('error', '')
            if error:
                error_message = error.get('message')
                if error_message == "Le code d'authentification est incorrect":
                    raise BrowserIncorrectPassword(error_message, bad_fields=['code'])

                self.clear_twofa_params()
                raise AssertionError(f'otp validation error: {error_message}')

            raise

        self.validate_twofa()

    def handle_sms(self):
        self.handle_code_otp(token=self.code)

    def handle_email(self):
        self.handle_code_otp(token=self.email_code)

    def handle_polling(self):
        # On boursobank validation by appVal is limited to 10 minutes
        twofa_validated = False
        for _ in polling_loop(timeout=600, delay=4):
            try:
                self.check_twofa_status()
            except HTTPNotFound as e:
                error = e.response.json().get('error', '')
                if error:
                    error_message = error.get('message')
                    if error_message == "La demande n'a pas abouti ou a été annulée":
                        raise AppValidationCancelled(error_message)

                    self.clear_twofa_params()
                    raise AssertionError(f'otp validation error: {error_message}')

                raise
            if self.page.is_success():
                twofa_validated = True
                break
            elif self.page.qrcode_needed():
                raise AuthMethodNotImplemented("La validation par QR code n'est pas encore prise en charge.")
            time.sleep(4)

        if not twofa_validated:
            raise AppValidationExpired()

        self.validate_twofa()

    def check_security_action_needed(self, error_message):
        security_message = re.compile(
            'bonnes pratiques de securite'
            + '|Protégez-vous contre la fraude'
        )

        if security_message.search(error_message):
            # error_message isn't explicit enough for the user to understand he has something to do
            raise ActionNeeded(
                locale="fr-FR",
                message='Un message relatif aux bonnes pratiques de sécurité nécessite une confirmation sur votre espace.',
                action_type=ActionType.ACKNOWLEDGE,
            )

        raise AssertionError('Unhandled error message : "%s"' % error_message)

    def init_login(self):
        self.twofa_count = 0
        self.start_login()

        if self.minor.is_here():
            error_message = self.page.get_error_message()
            # The full message here is "Votre dossier de passage à la majorité est validé ! Vous pourrez vous connecter à votre Espace Client dès votre majorité."
            if 'Votre dossier de passage à la majorité est validé' in error_message:
                raise BrowserUnavailable(error_message)
            # Here we raise an ActionNeeded because in this case the users will have 18 yo soon, and he have to update his informations
            # The message is hardcoded because the error_message in this case is really big and not really relevant.
            if 'nous devons collecter quelques éléments vous concernant.' in error_message:
                raise ActionNeeded(
                    locale="fr-FR", message='Vous avez 18 ans, veuillez mettre à jour votre dossier.',
                    action_type=ActionType.FILL_KYC,
                )
            if 'pas accessible aux jeunes de moins de 18 ans.' in error_message:
                raise BrowserUserBanned(self.page.get_error_message())
            # The full message here is " Vous pourrez accéder à vos comptes dans quelques minutes, pour cela, il vous suffit de finaliser la mise à jour de votre
            # dossier. Il vous reste à : Ensuite vous aurez accès à vos comptes et pourrez commander une Carte Bancaire : Si vous êtes détenteur de l'offre Freedom,
            # votre CB sera utilisable jusqu'à vos 18 ans et 2 mois, il sera donc important que vous commandiez une nouvelle carte en ligne pour continuer à profiter
            # d'un moyen de paiement chez Boursorama Banque"
            if 'finaliser la mise à jour de votre dossier' in error_message:
                raise ActionNeeded(
                    locale="fr-FR", message='Une mise à jour de votre dossier est nécessaire pour accéder à votre espace banque en ligne.',
                    action_type=ActionType.FILL_KYC,
                )
            if 'impératif que vous les complétiez dès à présent.' in error_message:
                raise ActionNeeded(locale="fr-FR", message=error_message, action_type=ActionType.FILL_KYC)
            raise AssertionError('Unhandled error message: %s' % error_message)
        elif self.error.is_here():
            messages = re.compile(
                'verrouille'
                + '|incident'
                + '|nous adresser'
                + '|desactive'
            )

            error_message = self.page.get_error_message()
            if messages.search(error_message):
                raise ActionNeeded(locale="fr-FR", message=error_message)

            self.check_security_action_needed(error_message)

        elif self.card_renewal.is_here():
            raise ActionNeeded(
                locale="fr-FR", message='Une confirmation pour le renouvellement de carte bancaire est nécessaire sur votre espace.',
                action_type=ActionType.ACKNOWLEDGE,
            )
        elif self.login.is_here():
            error = self.page.get_error()
            assert error, 'Should not be on login page without error message'

            is_wrongpass = re.search(
                "Identifiant ou mot de passe invalide"
                + "|Erreur d'authentification"
                + "|Cette valeur n'est pas valide"
                + "|votre identifiant ou votre mot de passe n'est pas valide",
                error
            )

            is_website_unavailable = re.search(
                "vous pouvez actuellement rencontrer des difficultés pour accéder à votre Espace Client"
                + "|Une erreur est survenue. Veuillez réessayer ultérieurement"
                + "|Maintenance en cours, merci de réessayer ultérieurement."
                + "|Oups, Il semble qu'une erreur soit survenue de notre côté"
                + "|Service momentanément indisponible",
                error
            )

            is_user_banned = re.search(
                "Compte bloqué Pour des raisons de sécurité"
                + "|Trop de tentatives de connexion ont échoué",
                error
            )

            if is_website_unavailable:
                raise BrowserUnavailable()
            elif is_wrongpass:
                raise BrowserIncorrectPassword(error)
            elif "pour changer votre mot de passe" in error:
                # this popup appears after few wrongpass errors and requires a password change
                raise BrowserPasswordExpired()
            elif is_user_banned:
                raise BrowserUserBanned(error)
            raise AssertionError('Unhandled error message : "%s"' % error)

        elif self.incident_trading_page.is_here():
            message = self.page.get_error_message()
            # Vous êtes actuellement en incident sur l'un de vos comptes.
            # En conséquence, nous vous demandons de bien vouloir régulariser cet incident par tous les moyens à votre disposition et dans les meilleurs délais.
            if message:
                raise ActionNeeded(locale="fr-FR", message=message, action_type=ActionType.PAYMENT)
            raise AssertionError("Land on incident page but didn't found any error message")

        # After login, we might be redirected to the two factor authentication page.
        self.handle_authentication()

    @retry(FormNotFound, tries=3, delay=3)
    def start_login(self):
        self.session.cookies.set("brsDomainMigration", "migrated")
        self.login.go()

        if not self.page.is_html_loaded():
            # If "__brs_mit" is not present, HTML responses are almost empty.
            # Page must be reloaded after we set the cookie.
            cookie_name, cookie_value = self.page.get_document_cookie()
            if not cookie_name or not cookie_value:
                raise AssertionError('Could not fetch "__brs_mit" cookie')
            self.session.cookies.set(cookie_name, cookie_value)
            self.login.go()

        self.page.enter_password(self.username, self.password)

    @login_method
    def do_login(self):
        return super(BoursoramaBrowser, self).do_login()

    def ownership_guesser(self, accounts_list):
        ownerless_accounts = [account for account in accounts_list if empty(account.ownership)]

        if ownerless_accounts:
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
        for account in accounts_list:
            if account.type == Account.TYPE_CARD and empty(account.parent.ownership):
                if account.parent in parent_accounts:
                    account.parent.ownership = AccountOwnership.CO_OWNER
                parent_accounts.append(account.parent)

        # We set all accounts without ownership as if they belong to the credential owner
        for account in accounts_list:
            if empty(account.ownership) and account.type != Account.TYPE_CARD:
                account.ownership = AccountOwnership.OWNER

        # Account cards should be set with the same ownership of their parents accounts
        for account in accounts_list:
            if account.type == Account.TYPE_CARD:
                account.ownership = account.parent.ownership

    @retry_on_logout()
    @need_login
    def get_accounts_list(self):
        self.status.go()

        accounts_list = None  # necessary to loop again after being logged out

        exc = None
        for _ in range(3):
            if accounts_list is not None:
                break

            accounts_list = []
            loans_list = []
            # Check that there is at least one account for this user
            has_account = False

            try:
                self.accounts.go()
            except BrowserUnavailable as e:
                self.logger.warning('par accounts seem unavailable, retrying')
                exc = e
                accounts_list = None
                continue
            else:
                if self.accounts.is_here():
                    accounts_list.extend(self.get_filled_accounts())
                    has_account = True
                else:
                    # We dont want to let has_account=False if we landed on an unknown page
                    # it has to be the no_accounts page
                    assert self.no_account.is_here()

                exc = None

            if not has_account:
                # if we landed twice on NoAccountPage, it means there is neither pro accounts nor pp accounts
                raise NoAccountsException()

            for account in list(accounts_list):
                if account.type == Account.TYPE_LOAN:
                    # Loans details are present on another page so we create
                    # a Loan object and remove the corresponding Account:
                    self.location(account.url)
                    loan = self.page.get_loan()
                    for attr in ['url', 'owner_type', 'bank_name']:
                        setattr(loan, attr, getattr(account, attr))
                    loans_list.append(loan)
                    accounts_list.remove(account)
            accounts_list.extend(loans_list)

            self.cards_list = [acc for acc in accounts_list if acc.type == Account.TYPE_CARD]
            for card in self.cards_list:
                self.card_information.go(webid=card._webid, key=card._key)
                if not self.card_information.is_here():
                    self.logger.warning('Should have been on CardInformationPage.')
                    accounts_list.remove(card)
                    continue
                card.number = self.page.get_card_number(card)

                if empty(card.number):
                    # Cards without a number are not activated yet.
                    self.logger.warning("Card account doesn't have a card number.")
                    accounts_list.remove(card)
                else:
                    # Here we need the card number to add more detail to the label.
                    card_number = Regexp(pattern=r'^.{12}(\d{4})$', default=None).filter(card.number)
                    if card_number:
                        card.label = f'XX{card_number} {card.label}'

            type_with_iban = (
                Account.TYPE_CHECKING,
                Account.TYPE_SAVINGS,
                Account.TYPE_MARKET,
                Account.TYPE_PEA,
            )
            for account in accounts_list:
                if account.type in type_with_iban:
                    account_iban = self.iban.go(webid=account._webid).get_iban()
                    # IBAN fetching can fail randomly, let us try to fetch it
                    # again if we did not get it right the first time.
                    if account_iban == NotAvailable:
                        account_iban = self.iban.go(webid=account._webid).get_iban()
                    account.iban = account_iban

            for card in self.cards_list:
                checking, = [
                    account
                    for account in accounts_list
                    if account.type == Account.TYPE_CHECKING and account.url in card.url
                ]
                card.parent = checking

        if exc:
            raise exc

        self.ownership_guesser(accounts_list)
        return accounts_list

    def get_filled_accounts(self):
        accounts_list = []
        # request for life insurance sometime failed, so we retry once just in case
        retrying_location = retry(ReadTimeout, tries=2)(self.location)
        for account in self.page.iter_accounts():
            try:
                # With failed life insurance request where we wait about 59 seconds to have a response from boursorama
                # The response is supposed to be 1 or 2s max. At first we set the TO at 5s but some user report that it
                # was too short for them so it is now set at 20s
                retrying_location(account.url, timeout=20)
            except requests.exceptions.HTTPError as e:
                # We do not yield life insurance accounts with a 404 or 503 error. Since we have verified, that
                # it is a website scoped problem and not a bad request from our part.
                # 404 is the original behavior. We could remove it in the future if it does not happen again.
                status_code = e.response.status_code
                if (
                    status_code in (404, 503)
                    and account.type == Account.TYPE_LIFE_INSURANCE
                ):
                    self.logger.warning(
                        '%s ! Broken link for life insurance account (%s). Account will be skipped',
                        status_code,
                        account.label,
                    )
                    continue
                raise

            self.page.fill_account(obj=account)
            if account.id:
                accounts_list.append(account)
        return accounts_list

    def get_account(self, account_id=None, account_iban=None):
        acc_list = self.get_accounts_list()
        account = strict_find_object(acc_list, id=account_id)
        if not account:
            account = strict_find_object(acc_list, iban=account_iban)
        return account

    def get_opening_date(self, account_url):
        self.location(account_url)
        return self.page.fetch_opening_date()

    def get_debit_date(self, debit_date):
        for i, j in zip(self.deferred_card_calendar, self.deferred_card_calendar[1:]):
            if i[0] < debit_date <= j[0]:
                return j[1]

    @retry_on_logout()
    @need_login
    def get_history(self, account, coming=False):
        if account.type in (
            Account.TYPE_LOAN,
            Account.TYPE_MORTGAGE,
            Account.TYPE_CONSUMER_CREDIT,
            Account.TYPE_DEPOSIT,
            Account.TYPE_REAL_ESTATE,
        ) or '/compte/derive' in account.url:
            return []
        if account.type is Account.TYPE_SAVINGS and "PLAN D'ÉPARGNE POPULAIRE" in account.label:
            return []
        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_MARKET, Account.TYPE_PER):
            return self.get_invest_transactions(account, coming)
        elif account.type == Account.TYPE_CARD:
            return self.get_card_transactions(account, coming)
        return self.get_regular_transactions(account, coming)

    def otp_location(self, *args, **kwargs):
        # this method is used in `otp_pagination` decorator from pages
        # without this header, we don't get a 401 but a 302 that logs us out
        kwargs.setdefault('headers', {}).update({'X-Requested-With': "XMLHttpRequest"})
        try:
            return super(BoursoramaBrowser, self).location(*args, **kwargs)
        except ClientError as e:
            # as done in boursorama's js : a 401 results in a popup
            # asking to send an otp to get more than x months of transactions
            # so... we don't want it :)
            if e.response.status_code != 401:
                raise e

    def get_regular_transactions(self, account, coming):
        # We look for 3 years of history.
        params = {}
        params['movementSearch[toDate]'] = (date.today() + relativedelta(days=40)).strftime('%d/%m/%Y')
        params['movementSearch[fromDate]'] = (date.today() - relativedelta(years=3)).strftime('%d/%m/%Y')
        params['movementSearch[advanced]'] = 1
        if self.otp_location('%s/mouvements' % account.url.rstrip('/'), params=params) is None:
            return

        for transaction in self.page.iter_history():
            # Useful for authorization transactions that can be found up to a few weeks
            if coming and transaction._is_coming and transaction.date > (date.today() - relativedelta(days=30)):
                yield transaction
            if coming and not transaction._is_coming:
                # coming transaction come first.
                # to avoid iterating on all transactions when
                # we look for comings only, we should stop
                # at the first history transaction.
                break
            elif not coming and not transaction._is_coming:
                yield transaction

    def get_html_past_card_transactions(self, account):
        """ Get card transactions from parent account page """

        self.otp_location('%s/mouvements' % account.parent.url.rstrip('/'))
        for tr in self.page.iter_history(is_card=False):
            # get card summaries
            if (
                tr.type == TransactionType.CARD_SUMMARY
                and account.number in tr.label  # in case of several cards per parent account
            ):
                tr.amount = - tr.amount
                yield tr

                # for each summaries, get detailed transactions
                self.location(tr._card_sum_detail_link)
                for detail_tr in self.page.iter_history():
                    detail_tr.bdate = detail_tr.date = tr.date
                    yield detail_tr

        # Note: Checking accounts have a 'Mes prélèvements à venir' tab,
        # but these transactions have no date anymore so we ignore them.

    def get_card_transaction(self, coming, tr):
        if coming and tr.date > date.today():
            tr._is_coming = True
            return True
        elif not coming and tr.date <= date.today():
            return True

    def get_card_transactions(self, account, coming):
        # All card transactions can be found in the CSV (history and coming),
        # however the CSV shows a maximum of 1000 transactions from all accounts.
        self.location(account.url)
        if self.home.is_here():
            # for some cards, the site redirects us to '/'...
            return

        if self.deferred_card_calendar is None and self.card_calendar_loaded:
            self.location(self.page.get_calendar_link())

        params = {}
        params['movementSearch[fromDate]'] = (date.today() - relativedelta(years=3)).strftime('%d/%m/%Y')
        params['fullSearch'] = 1
        if self.otp_location(account.url, params=params) is None:
            return

        csv_link = self.page.get_csv_link()
        if csv_link and self.otp_location(csv_link):
            # Yield past transactions as 'history' and
            # transactions in the future as 'coming':
            for tr in sorted_transactions(self.page.iter_history(account_number=account.number)):
                if self.get_card_transaction(coming, tr):
                    yield tr
        else:
            # if the export link is hidden or we got a 401 on csv link,
            # we need to get transactions from current page or we will just get nothing
            for tr in self.open(account.url).page.iter_history(is_card=True):
                if self.get_card_transaction(coming, tr):
                    yield tr

            if not coming:
                for tr in self.get_html_past_card_transactions(account):
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
        invest_account = (
            Account.TYPE_LIFE_INSURANCE,
            Account.TYPE_MARKET,
            Account.TYPE_PEA,
            Account.TYPE_PER,
            Account.TYPE_REAL_ESTATE,
        )

        if ('/compte/derive' not in account.url and account.type in invest_account):
            self.location(account.url)
            if account.type != Account.TYPE_REAL_ESTATE:
                liquidity = self.page.get_liquidity()
                if liquidity:
                    yield liquidity
                if self.page.has_gestion_profilee():
                    yield from self.page._iter_investment_gestion_profilee()

            yield from self.page.iter_investment()

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
        advisor.name = "Service clientèle"
        advisor.phone = "0146094949"
        return iter([advisor])

    def get_account_type_and_id(self, account_url):
        url = urlsplit(account_url)
        parts = [part for part in url.path.split('/') if part]

        assert len(parts) > 2, 'Account url missing some important part to iter recipient'
        account_type = parts[1]  # cav, ord, epargne ...
        if parts[-1] == 'mouvements-a-venir':
            # cards account have one part on the url
            account_webid = parts[-2]
        else:
            account_webid = parts[-1]

        return account_type, account_webid

    def go_recipients_list(self, account_url, account_id, for_scheduled=False):
        account_type, account_webid = self.get_account_type_and_id(account_url)

        # may raise a BrowserHTTPNotFound
        self.transfer_main_page.go(acc_type=account_type, webid=account_webid)

        # can check all account available transfer option
        if self.transfer_main_page.is_here():
            self.transfer_accounts.go(acc_type=account_type, webid=account_webid)

        if self.transfer_accounts.is_here():
            # may raise AccountNotFound
            self.page.submit_account(account_id)
        elif self.transfer_main_page.is_here():
            if for_scheduled:
                transfer_type = 'programme'
            else:
                transfer_type = 'immediat'
            self.new_transfer_wizard.go(
                acc_type=account_type,
                webid=account_webid,
                transfer_type=transfer_type
            )
            assert self.new_transfer_wizard.is_here(), 'Should be on recipients page'
            # may raise AccountNotFound
            self.page.submit_account(account_id)

        return account_type, account_webid

    @retry_on_logout()
    @need_login
    def iter_transfer_recipients(self, account, for_scheduled=False):
        if account.type in (Account.TYPE_LOAN, Account.TYPE_LIFE_INSURANCE):
            return []

        if not account.url:
            account = find_object(self.get_accounts_list(), iban=account.iban)
            assert account, 'Could not find an account with a matching iban'

        assert account.url, 'Account should have an url to access its recipients'

        try:
            self.go_recipients_list(account.url, account.id, for_scheduled)
        except (BrowserHTTPNotFound, AccountNotFound):
            return []

        assert (
            self.transfer_recipients_page.is_here()
            or self.new_transfer_wizard.is_here()
        ), 'Should be on recipients page'

        return self.page.iter_recipients()

    def check_basic_transfer(self, transfer):
        if transfer.date_type == TransferDateType.PERIODIC:
            raise NotImplementedError('Periodic transfer is not implemented')
        if transfer.amount <= 0:
            raise TransferInvalidAmount('transfer amount must be positive')
        if transfer.recipient_id == transfer.account_id:
            raise TransferInvalidRecipient('recipient must be different from emitter')
        if not transfer.label:
            raise TransferInvalidLabel('transfer label cannot be empty')

    @need_login
    def init_transfer(self, transfer, **kwargs):
        # Reset otp state when a new transfer is created
        self.transfer_form = None

        # Boursorama doesn't allow labels longer than 50 characters. To avoid
        # crash for such a useless problem, we truncate it.
        if len(transfer.label) > 50:
            self.logger.info('Truncating transfer label from "%s" to "%s"', transfer.label, transfer.label[:50])
            transfer.label = transfer.label[:50]

        # Transfer_date_type is set and used only for the new transfer wizard flow
        # the support for the old transfer wizard is left untouched as much as possible
        # until it can be removed.
        transfer_date_type = transfer.date_type
        if empty(transfer_date_type):
            if not empty(transfer.exec_date) and transfer.exec_date > date.today():
                transfer_date_type = TransferDateType.DEFERRED
            else:
                transfer_date_type = TransferDateType.FIRST_OPEN_DAY

        is_scheduled = (transfer_date_type in [TransferDateType.DEFERRED, TransferDateType.PERIODIC])

        self.check_basic_transfer(transfer)

        account = self.get_account(transfer.account_id, transfer.account_iban)
        if not account:
            raise AccountNotFound()

        recipients = list(self.iter_transfer_recipients(account, is_scheduled))
        if not recipients:
            raise TransferInvalidEmitter(
                message="Le compte émetteur ne permet pas d'effectuer des virements"
            )

        recipients = [rcpt for rcpt in recipients if rcpt.id == transfer.recipient_id]
        if len(recipients) == 0 and not empty(transfer.recipient_iban):
            # try to find recipients by iban:
            recipients = [rcpt for rcpt in recipients
                          if not empty(rcpt.iban) and rcpt.iban == transfer.recipient_iban]
        if len(recipients) == 0:
            raise TransferInvalidRecipient(
                message='Le compte émetteur ne peut pas faire de virement vers ce bénéficiaire'
            )
        assert len(recipients) == 1

        self.page.submit_recipient(recipients[0]._tempid)

        if self.transfer_characteristics.is_here():
            # Old transfer interface of Boursorama
            self.page.submit_info(transfer.amount, transfer.label, transfer.exec_date)
            assert self.transfer_confirm.is_here()

            if self.page.need_refresh():
                # In some case we are not yet in the transfer_characteristics page, you need to refresh the page
                self.location(self.url)
                assert not self.page.need_refresh()
            ret = self.page.get_transfer()

        else:
            # New transfer interface
            assert self.new_transfer_wizard.is_here()
            self.page.submit_amount(transfer.amount)

            assert self.new_transfer_wizard.is_here()

            error = self.page.get_amount_error()
            if error:
                raise TransferInvalidAmount(message=error)

            if is_scheduled:
                self.page.submit_programme_date_type(transfer_date_type)

            self.page.submit_info(transfer.label, transfer_date_type, transfer.exec_date)

            fees = NotLoaded
            if self.new_transfer_estimate_fees.is_here():
                fees = self.page.get_transfer_fee()
                self.page.submit()

            transfer_error = self.page.get_errors()
            if transfer_error:
                raise TransferBankError(message=transfer_error)
            assert self.new_transfer_confirm.is_here()
            ret = self.page.get_transfer()

        ## Last checks to ensure that the confirmation matches what was expected

        # at this stage, the site doesn't show the real ids/ibans, we can only guess
        if recipients[0].label != ret.recipient_label:
            self.logger.info(
                'Recipients from iter_recipient and from the transfer are different: "%s" and "%s"',
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
            raise TransferError('Account label changed during transfer (from "%s" to "%s")'
                                % (account.label, ret.account_label))

        ret.account_id = account.id
        ret.account_iban = account.iban

        if not empty(fees) and empty(ret.fees):
            ret.fees = fees

        return ret

    def otp_validation_continue_transfer(self, transfer, **kwargs):
        """Send any otp validation code that was provided to continue transfer

        This page should not have "@need_login", as a "relogin" would void
        the validity of any existing otp code.
        """
        otp_code = kwargs.get('otp_sms', kwargs.get('otp_email'))
        resume = kwargs.get('resume')
        if not otp_code and not resume:
            return False

        if not self.transfer_form:
            # The session expired
            raise TransferTimeout()

        # Continue a previously initiated transfer after an otp step
        # once the otp is validated, we should be redirected to the
        # transfer sent page
        self.send_otp_data(
            self.transfer_form,
            otp_code,
            TransferBankError,
            is_app=bool(resume),
        )
        self.transfer_form = None
        return True

    @need_login
    def execute_transfer(self, transfer, **kwargs):
        # If we are in the case of continuation after an otp, we will already
        # be on the transfer_sent page, otherwise, confirmation has to be sent
        if self.transfer_confirm.is_here() or self.new_transfer_confirm.is_here():
            self.page.submit()

        assert self.transfer_sent.is_here() or self.new_transfer_sent.is_here()

        transfer_error = self.page.get_errors()
        if transfer_error:
            raise TransferBankError(message=transfer_error)

        if not self.page.is_confirmed():
            # Check if an otp step might be needed initially or subsequently after a
            # previous otp step (ex.: email after sms)
            def raise_step(value):
                raise TransferStep(transfer, value)

            self.check_and_initiate_otp(
                None,
                store_form='transfer_form',
                raise_step=raise_step,
                resource=transfer,
            )

            # We haven't managed to raise an OTP exception, so we want to try
            # and raise an error depending on the alert.
            error = self.page.get_alert_message()
            if error:
                if 'fonds disponibles sont insuffisants' in error.casefold():
                    raise TransferInvalidAmount(message=error)

                raise AssertionError(
                    f'Unhandled error message in transfer sent page: {error}',
                )

            # We are not sure if the transfer was successful or not, so raise an error
            raise AssertionError('Confirmation message not found inside transfer sent page')

        # The last page contains no info, return the last transfer object from init_transfer
        return transfer

    @need_login
    def init_new_recipient(self, recipient):
        # so it is reset when a new recipient is added
        self.recipient_form = None

        # If an account was provided for the recipient, use it
        # otherwise use the first checking account available
        account = None
        for account in self.get_accounts_list():
            if not account.url:
                continue
            if recipient.origin_account_id is None:
                if account.type == Account.TYPE_CHECKING:
                    break
            elif account.id == recipient.origin_account_id:
                break
            elif (not empty(recipient.origin_account_iban)
                  and not empty(account.iban)
                  and account.iban == recipient.origin_account_iban):
                break
        else:
            raise AddRecipientBankError(
                message="Compte ne permettant pas l'ajout de bénéficiaires",
            )

        account_type, account_webid = self.get_account_type_and_id(account.url)
        self.recipients_page.go(type=account_type, webid=account_webid)

        assert self.recipients_page.is_here(), 'Should be on recipients page'

        self.rcpt_page.go(type=account_type, webid=account_webid)

        if self.page.is_type_choice():
            self.page.submit_choice_external_type()

        assert self.page.is_characteristics(), 'Not on the page to add recipients.'

        # fill recipient form
        self.page.submit_recipient(recipient)
        if recipient.origin_account_id is None:
            recipient.origin_account_id = account.id

        # Go to recipient confirmation page that will request to send an sms
        assert self.page.is_confirm_send_sms(), 'Cannot reach the page asking to send a sms.'
        self.page.confirm_send_sms()

        def raise_step(value):
            raise AddRecipientStep(recipient, value)

        self.check_and_initiate_otp(
            account.url,
            store_form='recipient_form',
            raise_step=raise_step,
            resource=recipient,
        )

        # in the unprobable case that no otp was needed, go on
        return self.check_and_update_recipient(recipient, account.url, account)

    def new_recipient(self, recipient, **kwargs):
        otp_code = kwargs.get('otp_sms', kwargs.get('otp_email'))
        resume = kwargs.get('resume')
        if not otp_code and not resume:
            # step 1 of new recipient
            return self.init_new_recipient(recipient)

        # step 2 of new_recipient
        if not self.recipient_form:
            # The session expired
            raise AddRecipientTimeout()

        # there is no confirmation to check the recipient
        # validating the sms code directly adds the recipient
        account_url = self.send_otp_data(
            self.recipient_form,
            otp_code,
            AddRecipientBankError,
            is_app=bool(resume),
        )
        self.recipient_form = None

        # Check if another otp step might be needed (ex.: email after sms)
        def raise_step(value):
            raise AddRecipientStep(recipient, value)

        self.check_and_initiate_otp(
            account_url,
            store_form='recipient_form',
            raise_step=raise_step,
            resource=recipient,
        )

        return self.check_and_update_recipient(recipient, account_url)

    def send_otp_data(self, otp_data, otp_code, exception, is_app=False):
        confirm_data = otp_data['confirm_data']
        url = confirm_data['url']
        confirm_params = {
            key: value for key, value in confirm_data.items()
            if key != 'url'
        }

        if not is_app:
            # Validate the OTP
            confirm_data['token'] = otp_code

            try:
                self.location(url, data=confirm_params)
            except ServerError as e:
                # If the code is invalid, we have an error 503
                if e.response.status_code == 503:
                    raise exception(message=e.response.json()['error']['message'])
                raise
        else:
            # Check the app validation status.
            for _ in polling_loop(timeout=620, delay=5):
                try:
                    self.location(url, json=confirm_params)
                except ClientError as exc:
                    if exc.response.status_code != 404:
                        raise

                    # "La demande d'authentification n'est pas valide" (61000)
                    # We consider this as an expired app validation, and there
                    # is no way to actually deny the authorization from the
                    # mobile app.
                    raise AppValidationExpired()

                if self.page.is_success():
                    break
            else:
                raise AppValidationExpired()

        del otp_data['confirm_data']
        account_url = otp_data.pop('account_url')

        # Continue the navigation by sending the form data
        # we saved.
        html_page_form = otp_data.pop('html_page_form')
        url = html_page_form.pop('url')
        self.location(url, data=html_page_form)

        return account_url

    def check_and_initiate_otp(
        self, account_url,
        store_form=None,
        raise_step=lambda value: None,
        resource=None,
    ):
        """Trigger otp if it is needed

        An otp will be requested after confirmation for adding a new recipient,
        transfering to an unregistered recipient, or sending an important amount
        (ex.: 60 000).
        Usually the otp is an sms, eventually followed by an email otp.
        Observed behaviors:
        - if the add recipient is restarted after the sms has been confirmed
        recently, the sms step is not presented again.
        - Sometimes after validating the sms code, the user is also asked to
        validate a code received by email (observed when adding a non-french
        recipient).

        :param store_form: Name of the property to store the form into.
        :param raise_step: Method to raise a step exception out of a value.
        """
        otp_exception = None
        otp_field_value = None
        if self.page.is_send_sms():
            otp_name = 'sms'
            otp_field_value = Value('otp_sms', label='Veuillez saisir le code reçu par sms')
        elif self.page.is_send_email():
            otp_name = 'email'
            otp_field_value = Value('otp_email', label='Veuillez saisir le code reçu par email')
        elif self.page.is_send_app():
            otp_exception = AppValidation(
                message="Veuillez valider dans l'application mobile.",
                resource=resource,
                expires_at=now_as_utc() + timedelta(minutes=10),
            )
        else:
            return

        otp_data = {'account_url': account_url}

        otp_data['html_page_form'] = self.page.get_confirm_otp_form()
        otp_data['confirm_data'] = self.page.get_confirm_otp_data()

        if self.page.send_otp():
            assert self.page.is_confirm_otp(), 'The %s was not sent.' % otp_name

        if store_form is not None:
            setattr(self, store_form, otp_data)

        if otp_exception:
            raise otp_exception

        raise_step(otp_field_value)

    def check_and_update_recipient(self, recipient, account_url, account=None):
        assert self.page.is_created(), 'The recipient was not added.'

        # At this point, the recipient was added to the website,
        # here we just want to return the right Recipient object.
        # We are taking it from the recipient list page
        # because there is no summary of the adding

        if not account:
            account = self.get_account(recipient.origin_account_id, recipient.origin_account_iban)
            if not account:
                raise AccountNotFound()

        self.go_recipients_list(account_url, account.id)
        assert (
            self.transfer_recipients_page.is_here()
            or self.new_transfer_wizard.is_here()
        ), 'Should be on recipients page'
        return find_object(self.page.iter_recipients(), iban=recipient.iban, error=RecipientNotFound)

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
        if self.transfer_main_page.is_here():
            self.new_transfer_wizard.go(acc_type='temp', webid='temp', transfer_type='immediat')
        return self.page.iter_emitters()

    @retry_on_logout()
    @need_login
    def iter_subscriptions(self):
        self.statements_page.go()
        return self.page.iter_subscriptions()

    @need_login
    def iter_documents(self, subscription):
        self.rib_page.go()
        for doc in self.page.get_document(subid=subscription.id):
            yield doc

        params = {
            'FiltersType[accountsKeys][]': subscription._account_key,
            'FiltersType[fromDate]': '01/01/1970',  # epoch, so we fetch as much as possible
            'FiltersType[toDate]': date.today().strftime("%d/%m/%Y"),
            'FiltersType[documentsTypes][]': ['cc', 'export_historic_statement', 'frais'],
            'FiltersType[buttons][submit]': '',
        }
        self.statements_page.go(params=params)

        for doc in self.page.iter_documents(subid=subscription.id):
            yield doc
