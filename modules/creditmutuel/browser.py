# Copyright(C) 2010-2011 Julien Veyssier
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

import re
import time
from datetime import datetime, timedelta
from itertools import groupby
from operator import attrgetter
from urllib.parse import urlparse
from dateutil import tz
from requests.exceptions import HTTPError, TooManyRedirects

from woob.capabilities.bill import Subscription
from woob.exceptions import (
    ActionNeeded, AppValidation, AppValidationExpired, AppValidationCancelled,
    AuthMethodNotImplemented, BrowserIncorrectPassword, BrowserUnavailable,
    BrowserQuestion, NeedInteractiveFor2FA, BrowserUserBanned, ActionType,
)
from woob.tools.value import Value
from woob.tools.capabilities.bank.transactions import FrenchTransaction, sorted_transactions
from woob.tools.decorators import retry
from woob.browser.browsers import need_login
from woob.browser.mfa import TwoFactorBrowser
from woob.browser.profiles import Wget
from woob.browser.url import URL
from woob.browser.pages import FormNotFound
from woob.browser.exceptions import ClientError, ServerError
from woob.capabilities.bank import (
    Account, AddRecipientStep, Recipient, AccountOwnership,
    AddRecipientTimeout, TransferStep, TransferBankError,
    AddRecipientBankError, TransferTimeout,
    AccountOwnerType, NoAccountsException
)
from woob.tools.capabilities.bank.investments import create_french_liquidity
from woob.tools.pdf import extract_text as extract_text_from_pdf
from woob.capabilities import NotAvailable
from woob.capabilities.base import find_object, empty
from woob.browser.filters.standard import QueryValue, Regexp

from .pages import (
    InfoDocPage, LoginPage, LoginErrorPage, AccountsPage, UserSpacePage,
    OperationsPage, CardPage, ComingPage, RecipientsListPage,
    ChangePasswordPage, VerifCodePage, EmptyPage, PorPage,
    IbanPage, NewHomePage, AdvisorPage, RedirectPage,
    LIAccountsPage, CardsActivityPage, CardsListPage,
    CardsOpePage, NewAccountsPage, InternalTransferPage,
    ExternalTransferPage, RevolvingLoanDetails, RevolvingLoansList,
    ErrorPage, SubscriptionPage, NewCardsListPage, NewCardsOpe, CardPage2, FiscalityConfirmationPage,
    ConditionsPage, MobileConfirmationPage, UselessPage, DecoupledStatePage, CancelDecoupled,
    OtpValidationPage, OtpBlockedErrorPage, TwoFAUnabledPage,
    LoansOperationsPage, LoansInsurancePage, OutagePage, PorInvestmentsPage, PorHistoryPage, PorHistoryDetailsPage,
    PorMarketOrdersPage, PorMarketOrderDetailsPage, SafeTransPage, InformationConfirmationPage,
    AuthorityManagementPage, DigipassPage, GeneralAssemblyPage, AuthenticationModePage, SolidarityPage,
)


__all__ = ['CreditMutuelBrowser']


class WrongBrowser(Exception):
    pass


class CreditMutuelBrowser(TwoFactorBrowser):
    PROFILE = Wget()
    TIMEOUT = 90
    BASEURL = 'https://www.creditmutuel.fr'
    HAS_CREDENTIALS_ONLY = True
    STATE_DURATION = 5
    TWOFA_DURATION = 60 * 24 * 90

    HAS_MULTI_BASEURL = False  # Some of the users will use CreditMutuel's BASEURL when others will use the child's url
    WRONG_BROWSER_EXCEPTION = WrongBrowser

    # connexion
    login = URL(
        r'/fr/authentification.html',
        r'/(?P<subbank>.*)fr/$',
        r'/(?P<subbank>.*)fr/banques/accueil.html',
        r'/(?P<subbank>.*)fr/banques/particuliers/index.html',
        LoginPage
    )
    login_error = URL(r'/(?P<subbank>.*)fr/identification/default.cgi', LoginErrorPage)
    outage_page = URL(r'/(?P<subbank>.*)fr/outage.html', OutagePage)
    twofa_unabled_page = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', TwoFAUnabledPage)
    digipass_page = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', DigipassPage)
    mobile_confirmation = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', MobileConfirmationPage)
    safetrans_page = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', SafeTransPage)
    decoupled_state = URL(r'/(?P<subbank>.*)fr/banque/async/otp/SOSD_OTP_GetTransactionState.htm', DecoupledStatePage)
    cancel_decoupled = URL(r'/(?P<subbank>.*)fr/banque/async/otp/SOSD_OTP_CancelTransaction.htm', CancelDecoupled)
    otp_validation_page = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', OtpValidationPage)
    otp_blocked_error_page = URL(r'/(?P<subbank>.*)fr/banque/validation.aspx', OtpBlockedErrorPage)
    fiscality = URL(r'/(?P<subbank>.*)fr/banque/residencefiscale.aspx', FiscalityConfirmationPage)
    authentication_mode = URL(r'/(?P<subbank>.*)fr/banque/ModeAuthentification.html', AuthenticationModePage)

    # accounts
    accounts = URL(
        r'/(?P<subbank>.*)fr/banque/situation_financiere.cgi',
        r'/(?P<subbank>.*)fr/banque/situation_financiere.html',
        AccountsPage
    )
    useless_page = URL(r'/(?P<subbank>.*)fr/banque/paci/defi-solidaire.html', UselessPage)

    revolving_loan_list = URL(
        r'/(?P<subbank>.*)fr/banque/CR/arrivee.asp\?fam=CR.*',
        r'/(?P<subbank>.*)fr/banque/arrivee.asp\?fam=CR.*',
        RevolvingLoansList
    )
    revolving_loan_details = URL(r'/(?P<subbank>.*)fr/banque/CR/cam9_vis_lstcpt.asp.*', RevolvingLoanDetails)
    user_space = URL(
        r'/(?P<subbank>.*)fr/banque/espace_personnel.aspx',
        r'/(?P<subbank>.*)fr/banque/accueil.cgi',
        r'/(?P<subbank>.*)fr/banque/DELG_Gestion',
        r'/(?P<subbank>.*)fr/banque/paci_engine/engine.aspx',
        r'/(?P<subbank>.*)fr/banque/paci_engine/static_content_manager.aspx',
        UserSpacePage
    )
    card = URL(
        r'/(?P<subbank>.*)fr/banque/operations_carte.cgi.*',
        r'/(?P<subbank>.*)fr/banque/mouvements.html\?webid=.*cardmonth=\d+$',
        r'/(?P<subbank>.*)fr/banque/mouvements.html.*webid=.*cardmonth=\d+.*cardid=',
        CardPage
    )
    operations = URL(
        r'/(?P<subbank>.*)fr/banque/mouvements.cgi.*',
        r'/(?P<subbank>.*)fr/banque/mouvements.html.*',
        r'/(?P<subbank>.*)fr/banque/nr/nr_devbooster.aspx.*',
        r'(?P<subbank>.*)fr/banque/CRP8_GESTPMONT.aspx\?webid=.*&trnref=.*&contract=\d+&cardid=.*&cardmonth=\d+',
        OperationsPage
    )

    # This loans_operations contains operation for some loans, but not all of them.
    loans_operations = URL(
        r'/(?P<subbank>.*)fr/banque/gec9.aspx.*',
        r'/(?P<subbank>.*)fr/banque/CR/consultation.asp\?webid=.*',
        LoansOperationsPage
    )
    loans_insurance = URL(
        r'/(?P<subbank>.*)fr/assurances/consultation/ASSEMPR.aspx',
        LoansInsurancePage
    )
    coming = URL(r'/(?P<subbank>.*)fr/banque/mvts_instance.cgi.*', ComingPage)
    info = URL(r'/(?P<subbank>.*)fr/banque/BAD.*', EmptyPage)
    change_pass = URL(r'/(?P<subbank>.*)fr/validation/change_password.cgi',
                      '/fr/services/change_password.html', ChangePasswordPage)
    verify_pass = URL(
        r'/(?P<subbank>.*)fr/validation/verif_code.cgi.*',
        r'/(?P<subbank>.*)fr/validation/lst_codes.cgi.*',
        VerifCodePage
    )
    new_home = URL(
        r'/(?P<subbank>.*)fr/banque/pageaccueil.html',
        r'/(?P<subbank>.*)banque/welcome_pack.html',
        NewHomePage
    )
    empty = URL(
        r'/(?P<subbank>.*)fr/banques/index.html',
        r'/(?P<subbank>.*)fr/banque/paci_beware_of_phishing.*',
        r'/(?P<subbank>.*)fr/validation/(?!change_password|verif_code|image_case|infos).*',
        EmptyPage
    )

    por = URL(
        r'/(?P<subbank>.*)fr/banque/SYNT_Synthese.aspx\?entete=1',
        r'/(?P<subbank>.*)fr/banque/PORT_Synthese.aspx',
        r'/(?P<subbank>.*)fr/banque/SYNT_Synthese.aspx',
        r'/(?P<subbank>.*)fr/banque/SYNT_AccueilBourse.aspx',
        PorPage
    )
    por_investments = URL(
        r'/(?P<subbank>.*)fr/banque/PORT_Valo.aspx',
        r'/(?P<subbank>.*)fr/banque/PORT_Valo.aspx\?&ddp=(?P<ddp>.*)',
        PorInvestmentsPage
    )
    por_history = URL(
        r'/(?P<subbank>.*)fr/banque/PORT_OperationsLst.aspx',
        r'/(?P<subbank>.*)fr/banque/PORT_OperationsLst.aspx\?&ddp=(?P<ddp>.*)',
        PorHistoryPage
    )
    por_history_details = URL(r'/(?P<subbank>.*)fr/banque/PORT_OperationsDet.aspx', PorHistoryDetailsPage)
    por_market_orders = URL(
        r'/(?P<subbank>.*)fr/banque/PORT_OrdresLst.aspx',
        r'/(?P<subbank>.*)fr/banque/PORT_OrdresLst.aspx\?&ddp=(?P<ddp>.*)',
        PorMarketOrdersPage
    )
    por_market_order_details = URL(r'/(?P<subbank>.*)fr/banque/PORT_OrdresDet.aspx', PorMarketOrderDetailsPage)
    por_action_needed = URL(r'/(?P<subbank>.*)fr/banque/ORDR_InfosGenerales.aspx', EmptyPage)

    li = URL(
        r'/(?P<subbank>.*)fr/assurances/profilass.aspx\?domaine=epargne',
        r'/(?P<subbank>.*)fr/assurances/(consultations?/)?WI_ASS.*',
        r'/(?P<subbank>.*)fr/assurances/WI_ASS',
        r'/(?P<subbank>.*)fr/assurances/SYNASSINT.aspx.*',
        r'/(?P<subbank>.*)fr/assurances/SYNASSVIE.aspx.*',
        r'/(?P<subbank>.*)fr/assurances/SYNASSINTNEXT.aspx.*',
        r'/fr/assurances/',
        LIAccountsPage
    )
    li_history = URL(
        r'/(?P<subbank>.*)fr/assurances/SYNASSVIE.aspx\?_tabi=C&_pid=ValueStep&_fid=GoOnglets&Id=3',
        LIAccountsPage
    )
    iban = URL(r'/(?P<subbank>.*)fr/banque/rib.cgi', IbanPage)

    new_accounts = URL(r'/(?P<subbank>.*)fr/banque/comptes-et-contrats.html', NewAccountsPage)
    new_operations = URL(
        r'/(?P<subbank>.*)fr/banque/mouvements.cgi',
        r'/fr/banque/nr/nr_devbooster.aspx.*',
        r'/(?P<subbank>.*)fr/banque/RE/aiguille(liste)?.asp',
        r'/fr/banque/mouvements.html',
        r'/(?P<subbank>.*)fr/banque/consultation/operations',
        r'/fr/banque/credit/operations/.*/consultation.aspx.*',
        r'/(?P<subbank>.*)fr/banque/credit/operations/.*/RE/consultation.aspx.*',
        OperationsPage
    )

    advisor = URL(
        r'/(?P<subbank>.*)fr/banques/contact/trouver-une-agence/(?P<page>.*)',
        r'/(?P<subbank>.*)fr/infoclient/',
        r'/(?P<subbank>.*)fr/banques/accueil/menu-droite/Details.aspx\?banque=.*',
        AdvisorPage
    )

    redirect = URL(r'/(?P<subbank>.*)fr/banque/paci_engine/static_content_manager.aspx', RedirectPage)

    cards_activity = URL(r'/(?P<subbank>.*)fr/banque/pro/ENC_liste_tiers.aspx', CardsActivityPage)
    cards_list = URL(
        r'/(?P<subbank>.*)fr/banque/pro/ENC_liste_ctr.*',
        r'/(?P<subbank>.*)fr/banque/pro/ENC_detail_ctr',
        CardsListPage
    )
    cards_ope = URL(r'/(?P<subbank>.*)fr/banque/pro/ENC_liste_oper', CardsOpePage)
    cards_ope2 = URL(r'/(?P<subbank>.*)fr/banque/CRP8_SCIM_DEPCAR.aspx', CardPage2)
    newcards_ope = URL(r'/(?P<subbank>.*)fr/banque/PCS3_SCIM_DEPCAR.aspx', NewCardsOpe)

    cards_hist_available = URL(
        r'/(?P<subbank>.*)fr/banque/SCIM_default.aspx\?_tabi=C&_stack=SCIM_ListeActivityStep%3a%3a&_pid=ListeCartes&_fid=ChangeList&Data_ServiceListDatas_CurrentType=MyCards',
        r'/(?P<subbank>.*)fr/banque/PCS1_CARDFUNCTIONS.aspx',
        r'/(?P<subbank>.*)fr/banque/PCS[25]_FUNCTIONS.aspx',
        NewCardsListPage
    )
    cards_hist_available2 = URL(r'/(?P<subbank>.*)fr/banque/SCIM_default.aspx', NewCardsListPage)

    internal_transfer = URL(r'/(?P<subbank>.*)fr/banque/virements/vplw_vi.html', InternalTransferPage)
    external_transfer = URL(r'/(?P<subbank>.*)fr/banque/virements/vplw_vee.html', ExternalTransferPage)
    recipients_list = URL(r'/(?P<subbank>.*)fr/banque/virements/vplw_bl.html', RecipientsListPage)
    error = URL(r'/(?P<subbank>.*)validation/infos.cgi', ErrorPage)

    subscription = URL(r'/(?P<subbank>.*)fr/banque/documentinternet.html', SubscriptionPage)
    terms_and_conditions = URL(
        r'/(?P<subbank>.*)fr/banque/conditions-generales.html',
        r'/(?P<subbank>.*)fr/banque/coordonnees_personnelles.aspx',
        r'/(?P<subbank>.*)fr/banque/paci_engine/paci_wsd_pdta.aspx',
        r'/(?P<subbank>.*)fr/banque/reglementation-dsp2.html',
        ConditionsPage
    )
    information_confirmation_page = URL(
        r'/(?P<subbank>.*)fr/client/paci_engine/information-client.html',
        InformationConfirmationPage
    )
    authority_management = URL(r'/(?P<subbank>.*)fr/banque/migr_gestion_pouvoirs.html', AuthorityManagementPage)
    solidarity = URL(
        r'/(?P<subbank>.*)fr/banque/paci_application_territoire_de_solidarite_p\d.html',
        r'/(?P<subbank>.*)fr/banque/paci_application_defi_solidaire_p\d.html',
        SolidarityPage,
    )

    general_assembly_page = URL(
        # Same URLs for all, but we can encounter different sub directory given
        # the website (cmag, cmmabn/fr, fr, ...)
        r'https://www.creditmutuel.fr/.+/assembleegenerale',
        GeneralAssemblyPage,
    )

    info_doc_page = URL(r'/(?P<subbank>.*)fr/banque/CMIG_Statut.aspx', InfoDocPage)

    currentSubBank = None
    is_new_website = None
    form = None
    need_clear_storage = None
    accounts_list = None

    def __init__(self, config, *args, **kwargs):
        self.config = config
        kwargs['username'] = self.config['login'].get()
        kwargs['password'] = self.config['password'].get()
        super(CreditMutuelBrowser, self).__init__(config, *args, **kwargs)

        self.__states__ = self.__states__ + (
            'currentSubBank', 'is_new_website',
            'need_clear_storage', 'recipient_form',
            'twofa_auth_state', 'polling_data', 'otp_data',
            'key_form', 'transfer_code_form',
        )

        self.twofa_auth_state = {}
        self.polling_data = {}
        self.otp_data = {}
        self.keep_session = None
        self.recipient_form = None
        self.key_form = None
        self.transfer_code_form = None

        self.AUTHENTICATION_METHODS = {
            'resume': self.handle_polling,
            'code': self.handle_sms,
        }

    def get_expire(self):
        """
        If 2FA is for 90 days for this client,
        self.twofa_auth_state is present and contains the exact time of the end of its validity
        Else, it will only last self.STATE_DURATION
        """
        if self.twofa_auth_state and self.twofa_auth_state.get('expires'):
            expires = datetime.fromtimestamp(
                self.twofa_auth_state['expires'], tz.tzlocal()
            ).replace(microsecond=0).isoformat()
            return expires
        return super(CreditMutuelBrowser, self).get_expire()

    def load_state(self, state):
        # when add recipient fails, state can't be reloaded.
        # If state is reloaded, there is this error message:
        # "Navigation interdite - Merci de bien vouloir recommencer votre action."
        if state.get('need_clear_storage'):
            # only keep 'twofa_auth_state' state to avoid new 2FA
            state = {'twofa_auth_state': state.get('twofa_auth_state')}

        if (
            state.get('polling_data')
            or state.get('recipient_form')
            or state.get('otp_data')
            or state.get('key_form')
            or state.get('transfer_code_form')
        ):
            # can't start on an url in the middle of a validation process
            # or server will cancel it and launch another one
            state.pop('url', None)

        # if state is empty (first login), it does nothing
        super(CreditMutuelBrowser, self).load_state(state)

    def finalize_twofa(self, twofa_data):
        """
        Go to validated 2FA url. Before following redirection,
        store 'auth_client_state' cookie to prove to server,
        for a TWOFA_DURATION, that 2FA is already done.
        """
        #retry to handle random ServerError on this url
        final_location = retry((ServerError, ConnectionError))(self.location)
        final_location(
            twofa_data['final_url'],
            data=twofa_data['final_url_params'],
            allow_redirects=False
        )

        for cookie in self.session.cookies:
            if cookie.name == 'auth_client_state':
                # only present if 2FA is valid for 90 days,
                # not present if 2FA is triggered systematically
                self.twofa_auth_state['value'] = cookie.value  # this is a token
                self.twofa_auth_state['expires'] = cookie.expires  # this is a timestamp
                if not cookie.expires:
                    self.logger.info(
                        "The expiration state of the twofa authentication cookie is null. Cookie details: expires=%s, value=%s",
                        cookie.expires,
                        cookie.value
                    )
                break
        else:
            self.logger.info("User probably has his account setup with a systematic sca")

        redirect_uri = self.response.headers.get('Location')
        if redirect_uri:
            self.location(redirect_uri)

    def handle_polling_redirection(self):
        """
        Handle case where decoupled page redirect us to an another page.
        """
        if self.login.is_here():
            # We are back to login page.
            raise AppValidationCancelled()
        if self.page.logged:
            # We are logged. Can continue with finalize_twofa.
            return

        raise AssertionError(f'Unhandled decoupled redirection. URL: {self.url}')

    def poll_decoupled(self, transactionId):
        """
        Poll decoupled on website.

        Raises AppValidationExpired or AppValidationCancelled on fail.
        """
        # 15' on website, we don't wait that much, but leave sufficient time for the user
        timeout = time.time() + 600.00  # 15' on webview, need not to wait that much
        data = {'transactionId': transactionId}

        while time.time() < timeout:
            #retry to handle random ServerError
            go_decoupled = retry(ServerError)(self.decoupled_state.go)
            go_decoupled(data=data, subbank=self.currentSubBank)

            if not self.decoupled_state.is_here():
                return self.handle_polling_redirection()

            decoupled_state = self.page.get_decoupled_state()
            if decoupled_state == 'VALIDATED':
                self.logger.info('AppValidation done, going to final_url')
                return
            elif decoupled_state in ('CANCELLED', 'NONE'):
                raise AppValidationCancelled()

            assert decoupled_state == 'PENDING', 'Unhandled polling state: "%s"' % decoupled_state
            time.sleep(5)  # every second on website, need to slow that down

        # manually cancel polling before website max duration for it
        self.cancel_decoupled.go(data=data, subbank=self.currentSubBank)
        raise AppValidationExpired()

    def handle_polling(self):
        if 'polling_id' not in self.polling_data:
            self.logger.info("Restarting login since we do not have the polling data")
            return self.init_login()

        try:
            self.poll_decoupled(self.polling_data['polling_id'])
            self.finalize_twofa(self.polling_data)
        finally:
            self.polling_data = {}

    def check_otp_blocked(self):
        # Too much wrong OTPs, locked down after total 3 wrong inputs
        if self.otp_blocked_error_page.is_here():
            error_msg = self.page.get_error_message()
            if "erreurs de saisie du code de confirmation" in error_msg:
                raise BrowserUserBanned(error_msg)
            raise BrowserUnavailable(error_msg)

    def handle_sms(self):
        if not self.otp_data or 'final_url_params' not in self.otp_data:
            raise BrowserIncorrectPassword("Le code de confirmation envoyé par SMS n'est plus utilisable")
        self.otp_data['final_url_params']['otp_password'] = self.code
        self.finalize_twofa(self.otp_data)

        if self.authority_management.is_here():
            self.page.skip_authority_management()

        # cases where 2FA is not finalized
        # Too much wrong OTPs, locked down after total 3 wrong inputs
        self.check_otp_blocked()

        # OTP is expired after 15', we end up on login page
        if self.login.is_here():
            raise BrowserIncorrectPassword("Le code de confirmation envoyé par SMS n'est plus utilisable")

        # Wrong OTP leads to same form with error message, re-raise BrowserQuestion
        elif self.otp_validation_page.is_here():
            error_msg = self.page.get_error_message()
            if 'erroné' not in error_msg:
                raise BrowserUnavailable(error_msg)
            else:
                label = '%s %s' % (error_msg, self.page.get_message())
                raise BrowserQuestion(Value('code', label=label))

        self.otp_data = {}

    def check_redirections(self):
        # 2FA pages might be coming, or not,
        # so we have to guess if interactive mode is needed,
        # and handle other kinds of redirections.

        location = self.response.headers.get('Location', '')

        if self.twofa_auth_state and self.twofa_auth_state.get('expires'):
            # 2FA validity date
            twofa_limit_date = datetime.fromtimestamp(self.twofa_auth_state['expires'])
        else:
            # case where 2FA is not done yet
            twofa_limit_date = datetime.now()
        twofa_limit_date = twofa_limit_date - timedelta(hours=2)  # 2h safety margin (in case of timezoning in backends)

        if (
            'validation.aspx' in location
            and not self.is_interactive
            and datetime.now() > twofa_limit_date
        ):
            # if 2FA not done yet, this ensures that we need user presence;
            # if it is done but soon to be invalidated, also need user;
            # if not interactive and 'validation.aspx' in location but still in 2FA validity
            # means we can skip_redo_twofa()
            self.twofa_auth_state = {}
            raise NeedInteractiveFor2FA()

        elif location:
            # Check if we still are on ConditionsPage,
            # keep following redirections until we have left this page.
            allow_redirects = any(string in location for string in [
                'conditions-generales',
                'paci_wsd_pdta',
                'static_content_manager',
                'paci',
            ])
            self.location(location, allow_redirects=allow_redirects)

    def check_auth_methods(self):
        self.get_current_sub_bank(force=True)

        if self.digipass_page.is_here():
            raise AuthMethodNotImplemented("La validation OTP par DIGIPASS n'est pas supportée.")

        if self.mobile_confirmation.is_here():
            self.page.check_bypass()
            if self.page.is_waiting_for_sca_activation():
                raise ActionNeeded(
                    locale="fr-FR", message="Une intervention de votre part est requise sur votre espace client.",
                    action_type=ActionType.ENABLE_MFA,
                )
            if self.mobile_confirmation.is_here():
                self.polling_data = self.page.get_polling_data()
                assert self.polling_data, "Can't proceed to polling if no polling_data"
                app_val_message = self.page.get_validation_msg()
                assert app_val_message, "Did not find any AppValidation message to share with the user."
                raise AppValidation(app_val_message)

        if self.safetrans_page.is_here():
            msg = self.page.get_safetrans_message()
            raise AuthMethodNotImplemented(msg)

        if self.otp_validation_page.is_here():
            self.otp_data = self.page.get_otp_data()
            assert self.otp_data, "Can't proceed to SMS handling if no otp_data"
            raise BrowserQuestion(Value('code', label=self.page.get_message()))

        self.check_otp_blocked()

    def init_login(self):
        # Retrying to avoid a random ServerError
        # while requesting login page
        go_login = retry((ServerError, ConnectionError))(self.login.go)
        go_login()

        # 2FA already done ; if valid, login() redirects to home page
        # 2FA might also now be systematic, this is handled with check_redirections()
        if self.twofa_auth_state:
            self.session.cookies.set(
                'auth_client_state',
                self.twofa_auth_state['value'],
                domain=urlparse(self.url).hostname,
            )

            self.page.login(self.username, self.password)

            self.check_redirections()
            # There could be two redirections to arrive to the mobile_confirmation page
            # Ex.: authentification.html -> pageaccueil.aspx -> validation.aspx
            self.check_redirections()

            if self.mobile_confirmation.is_here():
                # website proposes to redo 2FA when approaching end of its validity
                self.page.skip_redo_twofa()

            if self.information_confirmation_page.is_here():
                # If we reached this point, there is no SCA since:
                # - the user has to confirm its phone number
                # - or acknowledge a message about personal data settings being available in his client space
                link = self.page.get_confirmation_link()
                self.location(link)

        if self.authority_management.is_here():
            self.page.skip_authority_management()

        if self.solidarity.is_here():
            # it is a page that ask you to donate for disabled people
            raise ActionNeeded(
                    locale="fr-FR", message="Un message relatif au don pour les personnes en situation d'handicap est disponible sur votre espace.",
                    action_type=ActionType.ACKNOWLEDGE,
            )

        if not self.page.logged:
            # 302 redirect to catch to know if polling
            if self.login.is_here():

                # retry to handle random Server Error
                login = retry((ServerError, ConnectionError))(self.page.login)
                login(self.username, self.password)

                if self.login.is_here():
                    error_message = self.page.get_error_message()
                    if error_message:
                        # handle the case of the following error message:
                        # Vos droits d'accès sont échus. Veuillez vous rapprocher du mandataire principal de votre contrat.
                        if "Vos droits d'accès sont échus." in error_message:
                            raise ActionNeeded(error_message)
                        raise AssertionError(f"Unhandled login error : {error_message}")

                self.check_redirections()
                # There could be two redirections to arrive to the mobile_confirmation page
                self.check_redirections()

                if self.mobile_confirmation.is_here():
                    # website proposes to redo 2FA when approaching end of its validity
                    self.page.skip_redo_twofa()

            else:
                # in case client went from 90 days to systematic 2FA and self.is_interactive
                self.check_auth_methods()

            if self.outage_page.is_here():
                # The message in this page is informing the user of a service
                # outage. If we raise a BrowserUnavailable with the message, it might
                # look like it is our service that is unavailable. So we raise the error
                # without message.
                # Example of the message : Dans le cadre de l'amélioration de nos services,
                # nous vous informons que le service est interrompu jusqu'au 23/04/2020
                # à 02:30 environ. Veuillez nous excuser pour cette gêne momentanée.
                # Nous vous remercions de votre compréhension.
                raise BrowserUnavailable()

            if self.twofa_unabled_page.is_here():
                raise ActionNeeded(self.page.get_error_msg())

            if not self.page and not self.url.startswith(self.BASEURL):
                if self.HAS_MULTI_BASEURL:
                    # the psu selected another child module, this doesn't necessarily means he used the wrong creds
                    raise WrongBrowser()

                # when people try to log in but there are on a sub site of creditmutuel
                raise BrowserIncorrectPassword()

            if self.login_error.is_here():
                raise BrowserIncorrectPassword()

        if self.verify_pass.is_here():
            raise AuthMethodNotImplemented("L'identification renforcée avec la carte n'est pas supportée.")

        self.check_auth_methods()

        self.get_current_sub_bank(force=True)

        # This will log if the account is setup with a systematic 2FA, it will prevent
        # useless audit if/when user does not understand why they had a systematic 2FA.
        try:
            self.authentication_mode.go(subbank=self.currentSubBank)
        except (HTTPError, TooManyRedirects):
            self.logger.warning('We cannot access to the authentication setting page')
        else:
            if self.authentication_mode.is_here() and self.page.has_systematic_2fa():
                self.logger.warning('This connection is set up with systematic 2FA.')

    def ownership_guesser(self):
        profile = self.get_profile()
        psu_names = profile.name.lower().split()

        for account in self.accounts_list:
            label = account.label.lower()
            # We try to find "M ou Mme" or "Mlle XXX ou M XXXX" for example (non-exhaustive exemple list)
            if re.search(r'.* ((m) ([\w].*|ou )?(m[ml]e)|(m[ml]e) ([\w].*|ou )(m) ).*', label):
                account.ownership = AccountOwnership.CO_OWNER

            # We check if the PSU firstname and lastname is in the account label
            elif all(name in label.split() for name in psu_names):
                account.ownership = AccountOwnership.OWNER

        # Card Accounts should be set with the same ownership of their parents
        for account in self.accounts_list:
            if account.type == Account.TYPE_CARD and not empty(account.parent):
                account.ownership = account.parent.ownership

    @need_login
    def get_accounts_list(self):
        if not self.accounts_list:
            self.get_current_sub_bank()

            self.two_cards_page = None
            self.accounts_list = []
            self.revolving_accounts = []
            self.unavailablecards = []
            self.cards_histo_available = []
            self.cards_list = []
            self.cards_list2 = []

            default_owner_type = self.get_default_owner_type()

            # For some cards the validity information is only availaible on these 2 links
            self.cards_hist_available.go(subbank=self.currentSubBank)
            if self.cards_hist_available.is_here():
                self.unavailablecards.extend(self.page.get_unavailable_cards())
                for acc in self.page.iter_accounts():
                    acc._referer = self.cards_hist_available
                    self.accounts_list.append(acc)
                    self.cards_list.append(acc)
                    self.cards_histo_available.append(acc.id)

            if not self.cards_list:
                #retrying to handle random ServerError
                go_cards_hist_available2 = retry(ServerError)(self.cards_hist_available2.go)
                go_cards_hist_available2(subbank=self.currentSubBank)
                if self.cards_hist_available2.is_here():
                    self.unavailablecards.extend(self.page.get_unavailable_cards())
                    for acc in self.page.iter_accounts():
                        acc._referer = self.cards_hist_available2
                        self.accounts_list.append(acc)
                        self.cards_list.append(acc)
                        self.cards_histo_available.append(acc.id)

            # Handle cards on tiers page
            self.cards_activity.go(subbank=self.currentSubBank)
            companies = self.page.companies_link() if self.cards_activity.is_here() else \
                        [self.page] if self.is_new_website else []

            if not companies and self.page.has_cards():
                # if we have only 1 company we get its card list directly
                for card in self.page.iter_cards():
                    self.accounts_list.append(card)
                    self.cards_list2.append(card)
                self.cards_list.extend(self.cards_list2)
            else:
                for company in companies:
                    # We need to return to the main page to avoid navigation error
                    self.cards_activity.go(subbank=self.currentSubBank)
                    page = self.open(company).page if isinstance(company, str) else company
                    for card in page.iter_cards():

                        # This part was only tested on CIC (need to find connection with pro accounts)
                        self.location(card._link_id)
                        self.page.go_contract_details()
                        self.page.fill_card_numbers(card)

                        card2 = find_object(self.cards_list, id=card.id[:16])
                        if card2:
                            # In order to keep the id of the card from the old space, we exchange the following values
                            card._link_id = card2._link_id
                            if hasattr(card2, '_submit_button_name'):
                                card._submit_button_name = card2._submit_button_name
                            card._parent_id = card2._parent_id
                            card.coming = card2.coming
                            card._referer = card2._referer
                            card._secondpage = card2._secondpage
                            self.accounts_list.remove(card2)
                        self.accounts_list.append(card)
                        self.cards_list2.append(card)
                self.cards_list.extend(self.cards_list2)

            # Populate accounts from old website
            if not self.is_new_website:
                self.logger.warning('On old creditmutuel website')
                self.accounts.stay_or_go(subbank=self.currentSubBank)
                has_no_account = self.page.has_no_account()
                self.accounts_list.extend(self.page.iter_accounts())
                # Retrying to avoid a random ServerError on iban page
                go_iban = retry(ServerError)(self.iban.go)
                go_iban(subbank=self.currentSubBank).fill_iban(self.accounts_list)
                self.go_por_accounts()
                self.page.add_por_accounts(self.accounts_list)
            # Populate accounts from new website
            else:
                self.new_accounts.stay_or_go(subbank=self.currentSubBank)
                has_no_account = self.page.has_no_account()
                self.accounts_list.extend(self.page.iter_accounts())
                # Retrying to avoid a random ServerError on iban page
                go_iban = retry(ServerError)(self.iban.go)
                go_iban(subbank=self.currentSubBank).fill_iban(self.accounts_list)
                self.go_por_accounts()
                self.page.add_por_accounts(self.accounts_list)

            # if account is of type checking and has no iban, try to get it from documents
            for account in self.accounts_list:
                if account.type == Account.TYPE_CHECKING and empty(account.iban):
                    fake_sub = Subscription()
                    fake_sub.id = account.id
                    fake_sub.label = account.label
                    account_statements = [
                        doc for doc in self.iter_documents(fake_sub) if 'Extrait de comptes' in doc.label
                    ]
                    if not account_statements:
                        continue

                    content = self.open(account_statements[0].url).content
                    text = extract_text_from_pdf(content)
                    iban = Regexp(
                        pattern=r'IBAN : ([A-Z]{2}[0-9]{2}(?:[ ]?[0-9]{4}){5}(?:[ ]?[0-9]{3}))',
                        default='',
                    ).filter(text)

                    if iban:
                        account.iban = iban.replace(' ', '')

            # Retrying to avoid a random ServerError
            go_li = retry(ServerError)(self.li.go)
            go_li(subbank=self.currentSubBank)

            if self.page.has_accounts():
                self.page.go_accounts_list()
                for account in self.page.iter_li_accounts():
                    # The navigation is made through forms so we need to come back to the accounts list page
                    go_li(subbank=self.currentSubBank)
                    self.page.go_accounts_list()

                    # We can build the history and investments URLs using the account ID in the account details URL
                    if self.page.has_details(account):
                        self.page.go_account_details(account)
                        # The first tab is investments, the third tab is history
                        account._link_inv = self.url
                    else:
                        account._link_inv = None
                    account._link_id = None

                    self.accounts_list.append(account)

            # This type of account is like a loan, for splitting payments in smaller amounts.
            # Its history is irrelevant because money is debited from a checking account and
            # the balance is not even correct, so ignore it.
            excluded_label = ['etalis', 'valorisation totale']

            accounts_by_id = {}
            for acc in self.accounts_list:
                if empty(acc.owner_type):
                    acc.owner_type = default_owner_type

                if acc.label.lower() not in excluded_label:
                    accounts_by_id[acc.id] = acc

            for acc in self.accounts_list:
                # Set the parent to loans and cards accounts
                if acc.type == Account.TYPE_CARD and not empty(getattr(acc, '_parent_id', None)):
                    acc.parent = accounts_by_id.get(acc._parent_id, NotAvailable)

                elif acc.type in (Account.TYPE_MORTGAGE, Account.TYPE_LOAN):
                    if acc._parent_id:
                        acc.parent = accounts_by_id.get(acc._parent_id, NotAvailable)

                    # fetch loan insurance
                    if acc._insurance_url:
                        self.location(acc._insurance_url)
                        if self.page.is_insurance_page_available(acc):
                            self.page.get_insurance_details_page()
                            self.page.fill_insurance(obj=acc)
                        else:
                            error_message = self.page.get_error_message()

                            if error_message:
                                if "Vous n'avez pas l'autorisation d'accéder à ce contrat" in error_message:
                                    continue
                                if 'momentanément indisponible' in error_message:
                                    raise BrowserUnavailable(error_message)

                                raise AssertionError(f'Not handled error in insurance details page: {error_message}')

                elif acc.type == Account.TYPE_UNKNOWN:
                    self.logger.warning(
                        'There is an untyped account: please add "%s" to ACCOUNT_TYPES.',
                        acc.label
                    )


            self.accounts_list = list(accounts_by_id.values())

            if has_no_account and not self.accounts_list:
                raise NoAccountsException(has_no_account)

        self.ownership_guesser()

        return self.accounts_list

    @need_login
    def go_por_accounts(self):
        self.por.go(subbank=self.currentSubBank)

        # info page, appearing every 30 min
        # we can bypass without asking to never show it again
        message = self.page.get_action_needed_message()
        if message:
            if self.page.is_message_skippable:
                self.page.handle_skippable_action_needed()
            else:
                raise ActionNeeded(locale="fr-FR", message=message)

        # The info page popup may redirect us to the wrong tab
        # We make sure that the entete param is present in the url
        entete = QueryValue(None, 'entete', default='').filter(self.url)
        if entete != '1':
            self.por.go(subbank=self.currentSubBank)

    def get_account(self, _id):
        assert isinstance(_id, str)

        for a in self.get_accounts_list():
            if a.id == _id:
                return a

    def get_current_sub_bank(self, *, force=False):
        """Determine the current sub bank out of the URL we're currently on.

        :param force: Whether to redetermine the current sub bank if it is
                      already defined. This parameter was introduced for
                      compatibility with the previous approach.
        """
        if force or self.currentSubBank is None:
            # the account list and history urls depend on the sub bank of the user
            paths = urlparse(self.url).path.lstrip('/').split('/')
            self.currentSubBank = paths[0] + "/" if paths[0] != "fr" else ""
            if self.currentSubBank and paths[0] == 'banqueprivee' and paths[1] == 'mabanque':
                self.currentSubBank = 'banqueprivee/mabanque/'
            if self.currentSubBank and paths[1] == "decouverte":
                self.currentSubBank += paths[1] + "/"
            if paths[0] in ["cmmabn", "fr", "mabanque", "banqueprivee"]:
                self.is_new_website = True

        if (
            self.currentSubBank
            and self.currentSubBank.startswith('banqueprivee')
        ):
            self.logger.info('Is CIC Banque Privée: %r', self.currentSubBank)

    def list_operations(self, page, account):
        if isinstance(page, str):
            if page.startswith('/') or page.startswith('https') or page.startswith('?'):
                self.location(page)
            else:
                try:
                    self.location('%s/%sfr/banque/%s' % (self.BASEURL, self.currentSubBank, page))
                except ServerError as e:
                    self.logger.warning('Page cannot be visited: %s/%sfr/banque/%s: %s', self.BASEURL, self.currentSubBank, page, e)
                    raise BrowserUnavailable()
        else:
            self.page = page

        # On some savings accounts, the page lands on the contract tab, and we want the situation
        if account.type == Account.TYPE_SAVINGS and re.match(
            "Capital Expansion|Plan Epargne Logement", account.label
        ):
            self.page.go_on_history_tab()

        if self.li.is_here():
            return self.page.iter_history()

        if self.revolving_loan_list.is_here():
            # if we get redirected here, it means the account has no transactions
            return []

        if self.is_new_website and self.page:
            try:
                for page in range(1, 50):
                    # Need to reach the page with all transactions
                    if not self.page.has_more_operations():
                        break
                    form = self.page.get_form(xpath='//form[contains(@action, "_pid=AccountMasterDetail")]')
                    form['_FID_DoLoadMoreTransactions'] = ''
                    form['_wxf2_pseq'] = page
                    form.submit()
            # IndexError when form xpath returns [], StopIteration if next called on empty iterable
            except (StopIteration, FormNotFound):
                self.logger.warning('Could not get more history on new website')
            except IndexError:
                # 6 months history is not available
                pass

        while self.page:
            try:
                # Submit form if their is more transactions to fetch
                form = self.page.get_form(id="I1:fm")
                if self.page.doc.xpath('boolean(//a[@class="ei_loadmorebtn"])'):
                    form['_FID_DoLoadMoreTransactions'] = ""
                    form.submit()
                else:
                    break
            except (IndexError, FormNotFound):
                break
            # Sometimes the browser can't go further
            except ClientError as exc:
                if exc.response.status_code == 413:
                    break
                raise

        if not self.operations.is_here():
            return iter([])

        return self.pagination(lambda: self.page.get_history())

    def get_monthly_transactions(self, trs):
        date_getter = attrgetter('date')
        groups = [list(g) for k, g in groupby(sorted(trs, key=date_getter), date_getter)]
        trs = []
        for group in groups:
            if group[0].date > datetime.today().date():
                continue
            tr = FrenchTransaction()
            tr.raw = tr.label = "RELEVE CARTE %s" % group[0].date
            tr.amount = -sum(t.amount for t in group)
            tr.date = tr.rdate = tr.vdate = group[0].date
            tr.type = FrenchTransaction.TYPE_CARD_SUMMARY
            tr._is_coming = False
            tr._is_manualsum = True
            trs.append(tr)
        return trs

    @need_login
    def iter_market_orders(self, account):
        if all((
            account._is_inv,
            account.type in (Account.TYPE_MARKET, Account.TYPE_PEA),
            account._link_id,
        )):
            self.go_por_accounts()
            self.por_market_orders.go(subbank=self.currentSubBank, ddp=account._link_id)
            self.page.submit_date_range_form()
            if self.page.has_no_order():
                return
            orders = []
            page_index = 0
            # We stop at a maximum of 100 pages to avoid an infinite loop.
            while page_index < 100:
                page_index += 1
                for order in self.page.iter_market_orders():
                    orders.append(order)
                if not self.page.has_next_page():
                    break
                self.page.submit_next_page_form()
            for order in orders:
                if order._market_order_link:
                    self.location(order._market_order_link)
                    self.page.fill_market_order(obj=order)
                yield order

    @need_login
    def get_history(self, account):
        transactions = []

        if account._is_inv:
            if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA) and account._link_id:
                self.go_por_accounts()
                self.por_history.go(subbank=self.currentSubBank, ddp=account._link_id)
                self.page.submit_date_range_form()
                if self.page.has_no_transaction():
                    return
                page_index = 0
                # We stop at a maximum of 100 pages to avoid an infinite loop.
                while page_index < 100:
                    page_index += 1
                    for tr in self.page.iter_history():
                        transactions.append(tr)
                    if not self.page.has_next_page():
                        break
                    self.page.submit_next_page_form()
                for tr in transactions:
                    if tr._details_link:
                        self.location(tr._details_link)
                        self.page.fill_transaction(obj=tr)
                    yield tr
            elif account.type == Account.TYPE_LIFE_INSURANCE:
                if account._link_inv:
                    self.location(account._link_inv)
                    self.li_history.go(subbank=self.currentSubBank)
                    for tr in self.page.iter_history():
                        yield tr
            return

        if not account._link_id:
            if hasattr(account, '_submit_button_name'):
                account._referer.go(subbank=self.currentSubBank)
                self.page.go_to_operations_by_form(account)

                today = datetime.today()
                for tr in self.page.iter_history():
                    if tr.date > today:
                        tr._is_coming = True
                    yield tr
                return
            else:
                raise NotImplementedError()

        if len(account.id) >= 16 and account.id[:16] in self.cards_histo_available:
            self.logger.warning("Old card navigation with history available")
            if self.two_cards_page:
                # In this case, you need to return to the page where the iter account get the cards information
                # Indeed, for the same position of card in the two pages the url, headers and parameters are exactly the same
                account._referer.go(subbank=self.currentSubBank)
                if account._secondpage:
                    self.location(self.page.get_second_page_link())
            # Check if '000000xxxxxx0000' card have an annual history
            self.location(account._link_id)
            # The history of the card is available for 1 year with 1 month per page
            # Here we catch all the url needed to be the more compatible with the catch of merged subtransactions
            urlstogo = self.page.get_links()
            self.location(account._link_id)
            half_history = 'firstHalf'
            for url in urlstogo:
                transactions = []
                self.location(url)
                if 'GoMonthPrecedent' in url:
                    # To reach the 6 last month of history you need to change this url parameter
                    # Moreover we are on a transition page where we see the 6 next month (no scrapping here)
                    half_history = 'secondHalf'
                else:
                    history = self.page.get_history()
                    self.tr_date = self.page.get_date()
                    amount_summary = self.page.get_amount_summary()
                    if self.page.has_more_operations():
                        for i in range(1, 100):
                            # Arbitrary range; it's the number of click needed to access to the full history of the month (stop with the next break)
                            data = {
                                '_FID_DoAddElem': '',
                                '_wxf2_cc': 'fr-FR',
                                '_wxf2_pmode': 'Normal',
                                '_wxf2_pseq': i,
                                '_wxf2_ptarget': 'C:P:updPan',
                                'Data_ServiceListDatas_CurrentOtherCardThirdPartyNumber': '',
                                'Data_ServiceListDatas_CurrentType': 'MyCards',
                            }
                            if 'fid=GoMonth&mois=' in self.url:
                                m = re.search(r'fid=GoMonth&mois=(\d+)', self.url)
                                if m:
                                    m = m.group(1)
                                self.location('CRP8_SCIM_DEPCAR.aspx?_tabi=C&a__itaret=as=SCIM_ListeActivityStep\%3a\%3a\%2fSCIM_ListeRouter%3a%3a&a__mncret=SCIM_LST&a__ecpid=EID2011&_stack=_remote::moiSelectionner={},moiAfficher={},typeDepense=T&_pid=SCIM_DEPCAR_Details'.format(m, half_history), data=data)
                            else:
                                self.location(self.url, data=data)

                            if not self.page.has_more_operations_xml():
                                history = self.page.iter_history_xml(date=self.tr_date)
                                # We are now with an XML page with all the transactions of the month
                                break
                    else:
                        history = self.page.get_history(date=self.tr_date)

                    for tr in history:
                        # For regrouped transaction, we have to go through each one to get details
                        if tr._regroup:
                            self.location(tr._regroup)
                            for tr2 in self.page.get_tr_merged():
                                tr2._is_coming = tr._is_coming
                                tr2.date = self.tr_date
                                transactions.append(tr2)
                        else:
                            transactions.append(tr)

                    if transactions and self.tr_date < datetime.today().date():
                        tr = FrenchTransaction()
                        tr.raw = tr.label = "RELEVE CARTE %s" % self.tr_date
                        tr.amount = amount_summary
                        tr.date = tr.rdate = tr.vdate = self.tr_date
                        tr.type = FrenchTransaction.TYPE_CARD_SUMMARY
                        tr._is_coming = False
                        tr._is_manualsum = True
                        transactions.append(tr)

                    for tr in sorted_transactions(transactions):
                        yield tr

        else:
            # This gets the history for checking accounts
            # need to refresh the months select
            if account._link_id.startswith('ENC_liste_oper'):
                self.location(account._pre_link)
            elif account._link_id.startswith('/fr/banque/pro/ENC_liste_tiers'):
                self.location(account._link_id)
                transactions.extend(self.page.iter_cards_history())

            if not hasattr(account, '_card_pages'):
                for tr in self.list_operations(account._link_id, account):
                    transactions.append(tr)

            coming_link = self.page.get_coming_link() if self.operations.is_here() else None
            if coming_link is not None:
                for tr in self.list_operations(coming_link, account):
                    transactions.append(tr)

            deferred_date = None
            cards = ([page.select_card(account.number) for page in account._card_pages]
                     if hasattr(account, '_card_pages')
                     else account._card_links if hasattr(account, '_card_links') else [])
            for card in cards:
                card_trs = []
                for tr in self.list_operations(card, account):
                    if tr._to_delete:
                        # Delete main transaction when subtransactions exist
                        continue
                    if hasattr(tr, '_deferred_date') and (not deferred_date or tr._deferred_date < deferred_date):
                        deferred_date = tr._deferred_date
                    if tr.date >= datetime.now():
                        tr._is_coming = True
                    elif hasattr(account, '_card_pages'):
                        card_trs.append(tr)
                    transactions.append(tr)
                if card_trs:
                    transactions.extend(self.get_monthly_transactions(card_trs))

            if deferred_date is not None:
                # set deleted for card_summary
                for tr in transactions:
                    tr.deleted = (tr.type == FrenchTransaction.TYPE_CARD_SUMMARY
                                  and deferred_date.month <= tr.date.month
                                  and not hasattr(tr, '_is_manualsum'))

            for tr in sorted_transactions(transactions):
                yield tr

    @need_login
    def get_investment(self, account):
        if account._is_inv:
            if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA) and account._link_id:
                self.go_por_accounts()
                self.por_investments.go(subbank=self.currentSubBank, ddp=account._link_id)
            elif account.type == Account.TYPE_LIFE_INSURANCE:
                if not account._link_inv:
                    return []
                self.location(account._link_inv)
                if self.page.is_euro_fund():
                    return [self.page.create_euro_fund_invest(account.balance)]
            return self.page.iter_investment()
        if account.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
            liquidities = create_french_liquidity(account.balance)
            liquidities.label = account.label
            return [liquidities]
        return []

    @need_login
    def iter_recipients(self, origin_account):
        # access the transfer page
        self.internal_transfer.go(subbank=self.currentSubBank)
        if self.page.can_transfer(origin_account.id):
            for recipient in self.page.iter_recipients(origin_account=origin_account):
                yield recipient
        self.external_transfer.go(subbank=self.currentSubBank)
        if self.page.can_transfer(origin_account.id):
            origin_account._external_recipients = set()
            if self.page.has_transfer_categories():
                for category in self.page.iter_categories():
                    self.page.go_on_category(category['index'])
                    self.page.IS_PRO_PAGE = True
                    for recipient in self.page.iter_recipients(origin_account=origin_account, category=category['name']):
                        yield recipient
            else:
                for recipient in self.page.iter_recipients(origin_account=origin_account):
                    yield recipient

    def continue_transfer(self, transfer, **params):
        if 'Clé' in params:
            if not self.key_form:
                raise TransferTimeout(message="La validation du transfert par carte de clés personnelles a expiré")
            url = self.key_form.pop('url')
            self.format_personal_key_card_form(params['Clé'])
            self.location(url, data=self.key_form)
            self.key_form = None

            if self.verify_pass.is_here():
                # Do not reload state
                self.need_clear_storage = True
                error = self.page.get_error()
                if error:
                    raise TransferBankError(message=error)
                raise AssertionError('An error occured while checking the card code')

            if self.login.is_here():
                # User took too much time to input the personal key.
                raise TransferBankError(message="La validation du transfert par carte de clés personnelles a expiré")

            transfer_id = self.page.get_transfer_webid()
            if transfer_id and (empty(transfer.id) or transfer.id != transfer_id):
                transfer.id = self.page.get_transfer_webid()

        elif 'code' in params:
            code_form = self.transfer_code_form
            if not code_form:
                raise TransferTimeout(message="Le code de confirmation envoyé par SMS n'est plus utilisable")
            # Specific field of the confirmation page
            code_form['Bool:data_input_confirmationDoublon'] = 'true'
            self.send_sms(code_form, params['code'])
            self.transfer_code_form = None

            # OTP is expired after 15', we end up on login page
            if self.login.is_here():
                raise TransferBankError(message="Le code de confirmation envoyé par SMS n'est plus utilisable")

            transfer_id = self.page.get_transfer_webid()
            if transfer_id and (empty(transfer.id) or transfer.id != transfer_id):
                transfer.id = self.page.get_transfer_webid()

        elif 'resume' in params:
            self.poll_decoupled(self.polling_data['polling_id'])

            self.location(
                self.polling_data['final_url'],
                data=self.polling_data['final_url_params'],
            )
            self.polling_data = None

        transfer = self.check_and_initiate_transfer_otp(transfer)

        return transfer

    def check_and_initiate_transfer_otp(self, transfer, account=None, recipient=None):
        if self.page.needs_personal_key_card_validation():
            self.location(self.page.get_card_key_validation_link())
            error = self.page.get_personal_keys_error()
            if error:
                raise TransferBankError(message=error)

            self.key_form = self.page.get_personal_key_card_code_form()
            raise TransferStep(
                transfer,
                Value('Clé', label=self.page.get_question())
            )

        if account and transfer:
            transfer = self.page.handle_response_create_transfer(
                account, recipient, transfer.amount, transfer.label, transfer.exec_date
            )
        else:
            transfer = self.page.handle_response_reuse_transfer(transfer)

        if self.page.needs_otp_validation():
            self.transfer_code_form = self.page.get_transfer_code_form()
            raise TransferStep(
                transfer,
                Value('code', label='Veuillez saisir le code reçu par sms pour confirmer votre opération')
            )

        # The app validation, if needed, could have already been started
        # (for example, after validating the personal key card code).
        msg = self.page.get_validation_msg()
        if msg:
            self.polling_data = self.page.get_polling_data(form_xpath='//form[contains(@action, "virements")]')
            assert self.polling_data, "Can't proceed without polling data"
            raise AppValidation(
                resource=transfer,
                message=msg,
            )

        return transfer

    @need_login
    def init_transfer(self, transfer, account, recipient):
        if recipient.category != 'Interne':
            self.external_transfer.go(subbank=self.currentSubBank)
        else:
            self.internal_transfer.go(subbank=self.currentSubBank)

        if self.external_transfer.is_here() and self.page.has_transfer_categories():
            for category in self.page.iter_categories():
                if category['name'] == recipient.category:
                    self.page.go_on_category(category['index'])
                    break
            self.page.IS_PRO_PAGE = True
            self.page.RECIPIENT_STRING = 'data_input_indiceBen'

        self.page.prepare_transfer(account, recipient, transfer.amount, transfer.label, transfer.exec_date)

        new_transfer = self.check_and_initiate_transfer_otp(transfer, account, recipient)
        return new_transfer

    @need_login
    def execute_transfer(self, transfer, **params):
        # If we just did a transfer to a new recipient the transfer has already
        # been confirmed because of the app validation or the sms otp
        # Otherwise, do the confirmation when still needed
        if self.page.doc.xpath(
            '//form[@id="P:F"]//input[@type="submit" and contains(@value, "Confirmer")]'
        ):
            form = self.page.get_form(id='P:F', submit='//input[@type="submit" and contains(@value, "Confirmer")]')
            # For the moment, don't ask the user if he confirms the duplicate.
            form['Bool:data_input_confirmationDoublon'] = 'true'
            form.submit()

        return self.page.create_transfer(transfer)

    def get_default_owner_type(self):
        if self.is_new_website:
            # Retrying to avoid a random ServerError
            go_new_accounts = retry(ServerError)(self.new_accounts.go)
            go_new_accounts(subbank=self.currentSubBank)
            if self.page.business_advisor_intro():
                return AccountOwnerType.ORGANIZATION
            elif self.page.private_advisor_intro():
                return AccountOwnerType.PRIVATE
        self.logger.warning("Could not find a default owner type")

    @need_login
    def get_advisor(self):
        advisor = None
        if not self.is_new_website:
            self.logger.info('On old creditmutuel website')
            self.accounts.stay_or_go(subbank=self.currentSubBank)
            if self.page.get_advisor_link():
                advisor = self.page.get_advisor()
                self.location(self.page.get_advisor_link()).page.update_advisor(advisor)
        else:
            advisor = self.new_accounts.stay_or_go(subbank=self.currentSubBank).get_advisor()
            link = self.page.get_agency()
            if link:
                link = link.replace(':443/', '/')
                self.location(link)
                self.page.update_advisor(advisor)
        return iter([advisor]) if advisor else iter([])

    @need_login
    def get_profile(self):
        if not self.is_new_website:
            self.logger.info('On old creditmutuel website')
            profile = self.accounts.stay_or_go(subbank=self.currentSubBank).get_profile()
        else:
            profile = self.new_accounts.stay_or_go(subbank=self.currentSubBank).get_profile()
        return profile

    def get_recipient_object(self, recipient):
        r = Recipient()
        r.iban = recipient.iban
        r.id = recipient.iban
        r.label = recipient.label
        r.category = recipient.category
        # On credit mutuel recipients are immediatly available.
        r.enabled_at = datetime.now().replace(microsecond=0)
        r.currency = 'EUR'
        r.bank_name = NotAvailable
        return r

    def format_personal_key_card_form(self, key):
        self.key_form['[t:xsd%3astring;]Data_KeyInput'] = key

        # we don't know the card id
        # by default all users have only one card
        # but to be sure, let's get it dynamically
        do_validate = [k for k in self.key_form.keys() if '_FID_DoValidate_cardId' in k]
        assert len(do_validate) == 1, 'There should be only one card.'
        self.key_form[do_validate[0]] = ''

        activate = [k for k in self.key_form.keys() if '_FID_GoCardAction_action' in k]
        for k in activate:
            del self.key_form[k]

    def continue_new_recipient(self, recipient, **params):
        if 'Clé' in params:
            if not self.key_form:
                raise AddRecipientTimeout(message="La validation par carte de clés personnelles a expiré")
            url = self.key_form.pop('url')
            self.format_personal_key_card_form(params['Clé'])
            self.location(url, data=self.key_form)
            self.key_form = None

            if self.verify_pass.is_here():
                # Do not reload state
                self.need_clear_storage = True
                error = self.page.get_error()
                if error:
                    raise AddRecipientBankError(message=error)
                raise AssertionError('An error occured while checking the card code')

            if self.login.is_here():
                # User took too much time to input the personal key.
                raise AddRecipientBankError(message="La validation par carte de clés personnelles a expiré")

        self.page.add_recipient(recipient)
        if self.page.bic_needed():
            self.page.ask_bic(self.get_recipient_object(recipient))
        self.page.ask_auth_validation(self.get_recipient_object(recipient))

    def send_sms(self, form, sms):
        url = form.pop('url')
        form['otp_password'] = sms
        form['_FID_DoConfirm.x'] = '1'
        form['_FID_DoConfirm.y'] = '1'
        form['global_backup_hidden_key'] = ''
        self.location(url, data=form)

    def send_decoupled(self, form):
        url = form.pop('url')
        transactionId = form.pop('transactionId')

        self.poll_decoupled(transactionId)

        form['_FID_DoConfirm.x'] = '1'
        form['_FID_DoConfirm.y'] = '1'
        form['global_backup_hidden_key'] = ''
        self.location(url, data=form)

    def end_new_recipient_with_auth_validation(self, recipient, **params):
        if 'code' in params:
            if not self.recipient_form:
                raise AddRecipientTimeout(message="Le code de confirmation envoyé par SMS n'est plus utilisable")
            self.send_sms(self.recipient_form, params['code'])

        elif 'resume' in params:
            if not self.recipient_form:
                raise AddRecipientTimeout(message="Le demande de confirmation a expiré")
            self.send_decoupled(self.recipient_form)

        self.recipient_form = None
        self.page = None
        return self.get_recipient_object(recipient)

    def post_with_bic(self, recipient, **params):
        data = {}
        for k, v in self.recipient_form.items():
            if k != 'url':
                data[k] = v
        data['[t:dbt%3astring;x(11)]data_input_BIC'] = params['Bic']
        self.location(self.recipient_form['url'], data=data)
        self.page.ask_auth_validation(self.get_recipient_object(recipient))

    def set_new_recipient(self, recipient, **params):
        self.get_current_sub_bank()

        if 'Bic' in params:
            return self.post_with_bic(recipient, **params)
        if 'code' in params or 'resume' in params:
            recipient = self.end_new_recipient_with_auth_validation(recipient, **params)
            if not self.is_new_website:
                self.accounts.go(subbank=self.currentSubBank)
            else:
                self.new_accounts.go(subbank=self.currentSubBank)
            return recipient
        if 'Clé' in params:
            return self.continue_new_recipient(recipient, **params)

        assert False, 'An error occured while adding a recipient.'

    @need_login
    def new_recipient(self, recipient, **params):
        self.get_current_sub_bank()

        self.recipients_list.go(subbank=self.currentSubBank)
        if self.page.has_list():
            assert recipient.category in self.page.get_recipients_list(), \
                'Recipient category "%s" is not on the website available list.' % recipient.category
            self.page.go_list(recipient.category)

        self.page.go_to_add()
        if self.verify_pass.is_here():
            error = self.page.get_personal_keys_error()
            if error:
                raise AddRecipientBankError(message=error)

            self.key_form = self.page.get_personal_key_card_code_form()
            raise AddRecipientStep(self.get_recipient_object(recipient), Value('Clé', label=self.page.get_question()))
        else:
            return self.continue_new_recipient(recipient, **params)

    @need_login
    def iter_subscriptions(self):
        for account in self.get_accounts_list():
            sub = Subscription()
            sub.id = account.id
            sub.label = account.label
            yield sub

    @need_login
    def iter_documents(self, subscription):
        return self.iter_documents_for_account(subscription.id, subscription.label)

    def iter_documents_for_account(self, account_id, account_label):
        self.get_current_sub_bank()

        # Retrying to avoid a random ServerError on iban page
        go_iban = retry(ServerError)(self.iban.go)
        go_iban(subbank=self.currentSubBank)
        iban_document = self.page.get_iban_document(account_label, account_id)
        if iban_document:
            yield iban_document
        #retrying to avoid random Server error
        go_subscription = retry(ServerError)(self.subscription.go)
        go_subscription(subbank=self.currentSubBank, params={'typ': 'doc'})

        if self.info_doc_page.is_here():
            # Same precedent request is sufficient to skip the redirected page, with no relevant information, we're on
            go_subscription(subbank=self.currentSubBank, params={'typ': 'doc'})

        access_not_allowed_msg = "Vous ne disposez pas des droits nécessaires pour accéder à cette partie de l'application."
        if access_not_allowed_msg in self.page.error_msg():
            self.logger.warning("Bank user account has insufficient right to access the documents page")
            return

        link_to_bank_statements = self.page.get_link_to_bank_statements()
        if not link_to_bank_statements:
            return

        self.location(link_to_bank_statements)
        security_limit = 10

        internal_account_id = self.page.get_internal_account_id_to_filter_subscription(account_id)
        if internal_account_id:
            params = {
                '_pid': 'SelectDocument',
                '_tabi': 'C',
                'k_crit': 'CTRREF={}'.format(internal_account_id),
                'k_typePageDoc': 'DocsFavoris'
            }
            for i in range(security_limit):
                params['k_numPage'] = i + 1
                # there is no way to match a document to a subscription for sure
                # so we have to ask the bank only documents for the wanted subscription

                go_subscription(params=params, subbank=self.currentSubBank)
                for doc in self.page.iter_documents(sub_id=account_id):
                    yield doc

                if self.page.is_last_page():
                    break

    @need_login
    def iter_emitters(self):
        """
        Go to both internal and external transfer pages in case some accounts
        are only allowed to do internal transfers.
        """
        emitter_ids = []
        self.get_current_sub_bank(force=True)
        if not self.is_new_website:
            self.logger.info('On old creditmutuel website')
            raise NotImplementedError()

        self.internal_transfer.go(subbank=self.currentSubBank)
        for internal_emitter in self.page.iter_emitters():
            emitter_ids.append(internal_emitter.id)
            yield internal_emitter

        self.external_transfer.go(subbank=self.currentSubBank)
        for external_emitter in self.page.iter_emitters():
            if external_emitter.id not in emitter_ids:
                yield internal_emitter
