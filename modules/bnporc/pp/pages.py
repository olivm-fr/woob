# -*- coding: utf-8 -*-

# Copyright(C) 2009-2016  Romain Bignon
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

from collections import Counter
import re
from io import BytesIO
from random import randint
from decimal import Decimal
from datetime import datetime, timedelta
import lxml.html as html
from requests.exceptions import ConnectionError

from weboob.browser.elements import DictElement, ListElement, TableElement, ItemElement, method
from weboob.browser.filters.json import Dict
from weboob.browser.filters.standard import (
    Format, Eval, Regexp, CleanText, Date, CleanDecimal,
    Field, Coalesce, Map, MapIn, Env, Currency, FromTimestamp,
)
from weboob.browser.filters.html import TableCell
from weboob.browser.pages import JsonPage, LoggedPage, HTMLPage, PartialHTMLPage
from weboob.capabilities import NotAvailable
from weboob.capabilities.bank import (
    Account, Recipient, Transfer, TransferBankError,
    AddRecipientBankError, AccountOwnership,
    Emitter, EmitterNumberType, TransferStatus,
    TransferDateType,
)
from weboob.capabilities.wealth import (
    Investment, MarketOrder, MarketOrderDirection,
)
from weboob.capabilities.base import empty
from weboob.capabilities.contact import Advisor
from weboob.capabilities.profile import Person, ProfileMissing
from weboob.exceptions import (
    BrowserIncorrectPassword, BrowserUnavailable,
    BrowserPasswordExpired, ActionNeeded,
    AppValidationCancelled, AppValidationExpired,
)
from weboob.tools.capabilities.bank.iban import rib2iban, rebuild_rib, is_iban_valid
from weboob.tools.capabilities.bank.transactions import FrenchTransaction, parse_with_patterns
from weboob.tools.captcha.virtkeyboard import GridVirtKeyboard
from weboob.tools.date import parse_french_date
from weboob.tools.capabilities.bank.investments import is_isin_valid, IsinCode
from weboob.tools.compat import unquote_plus
from weboob.tools.html import html2text


class TransferAssertionError(Exception):
    pass


class ConnectionThresholdPage(HTMLPage):
    NOT_REUSABLE_PASSWORDS_COUNT = 3
    """BNP disallows to reuse one of the three last used passwords."""
    def make_date(self, yy, m, d):
        current = datetime.now().year
        if yy > current - 2000:
            yyyy = 1900 + yy
        else:
            yyyy = 2000 + yy
        return datetime(yyyy, m, d)

    def looks_legit(self, password):
        # the site says:
        # have at least 3 different digits
        if len(Counter(password)) < 3:
            return False

        # not the birthdate (but we don't know it)
        first, mm, end = map(int, (password[0:2], password[2:4], password[4:6]))
        now = datetime.now()
        try:
            delta = now - self.make_date(first, mm, end)
        except ValueError:
            pass
        else:
            if 10 < delta.days / 365 < 70:
                return False

        try:
            delta = now - self.make_date(end, mm, first)
        except ValueError:
            pass
        else:
            if 10 < delta.days / 365 < 70:
                return False

        # no sequence (more than 4 digits?)
        password = list(map(int, password))
        up = 0
        down = 0
        for a, b in zip(password[:-1], password[1:]):
            up += int(a + 1 == b)
            down += int(a - 1 == b)
        if up >= 4 or down >= 4:
            return False

        return True

    def on_load(self):
        msg = (
            CleanText('//div[@class="confirmation"]//span[span]')(self.doc)
            or CleanText('//p[contains(text(), "Vous avez atteint la date de fin de vie de votre code secret")]')(
                self.doc
            )
        )

        self.logger.warning('Password expired.')
        if not self.browser.rotating_password:
            raise BrowserPasswordExpired(msg)

        if not self.looks_legit(self.browser.password):
            # we may not be able to restore the password, so reject it
            self.logger.warning('Unable to restore it, it is not legit.')
            raise BrowserPasswordExpired(msg)

        new_passwords = []
        for i in range(self.NOT_REUSABLE_PASSWORDS_COUNT):
            new_pass = ''.join(str((int(char) + i + 1) % 10) for char in self.browser.password)
            if not self.looks_legit(new_pass):
                self.logger.warning('One of rotating password is not legit')
                raise BrowserPasswordExpired(msg)
            new_passwords.append(new_pass)

        current_password = self.browser.password
        for new_pass in new_passwords:
            self.logger.warning('Renewing with temp password')
            if not self.browser.change_pass(current_password, new_pass):
                self.logger.warning('New temp password is rejected, giving up')
                raise BrowserPasswordExpired(msg)
            current_password = new_pass

        if not self.browser.change_pass(current_password, self.browser.password):
            self.logger.error('Could not restore old password!')

        self.logger.warning('Old password restored.')

        # we don't want to try to rotate password two times in a row
        self.browser.rotating_password = 0


def cast(x, typ, default=None):
    try:
        return typ(x or default)
    except ValueError:
        return default


class BNPKeyboard(GridVirtKeyboard):
    color = (0x1f, 0x27, 0x28)
    margin = 3, 3
    symbols = {
        '0': '43b2227b92e0546d742a1f087015e487',
        '1': '2914e8cc694de26756096d0d0d4c6e0f',
        '2': 'aac54304a7bb850805d29f54557be366',
        '3': '0376d9f8419efee42e253d195a152547',
        '4': '3719595f15b1ac1c5a73d84aa290b5f6',
        '5': '617597f07a6530479927536671485439',
        '6': '4f5dce7bd0d9213fdae54b79bb8dd33a',
        '7': '49e07fa52b9bcee798f3a663f86e6cc1',
        '8': 'c60b723b3d95a46416b34c2cbefba3ed',
        '9': 'a13b8c3617a7bf854590833ddfb97f1f',
    }

    def __init__(self, browser, image):
        symbols = list('%02d' % x for x in range(1, 11))

        super(BNPKeyboard, self).__init__(symbols, 5, 2, BytesIO(image.content), self.color, convert='RGB')
        self.check_symbols(self.symbols, browser.responses_dirname)


class ListErrorPage(JsonPage):
    def get_error_message(self, error):
        key = 'app.identification.erreur.' + str(error)
        try:
            return html2text(self.doc[key])
        except KeyError:
            return None


class LoginPage(JsonPage):
    @staticmethod
    def render_template(tmpl, **values):
        for k, v in values.items():
            tmpl = tmpl.replace('{{ ' + k + ' }}', v)
        return tmpl

    @staticmethod
    def generate_token(length=11):
        chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXTZabcdefghiklmnopqrstuvwxyz'
        return ''.join((chars[randint(0, len(chars) - 1)] for _ in range(length)))

    def build_doc(self, text):
        try:
            return super(LoginPage, self).build_doc(text)
        except ValueError:
            # XXX When login is successful, server sends HTML instead of JSON,
            #     we can ignore it.
            return {}

    def on_load(self):
        if self.url.startswith('https://mabanqueprivee.'):
            self.browser.switch('mabanqueprivee')

        # Some kind of internal server error instead of normal wrongpass errorCode.
        if self.get('errorCode') == 'INTO_FACADE ERROR: JDF_GENERIC_EXCEPTION':
            raise BrowserIncorrectPassword()

        error = cast(self.get('errorCode', self.get('codeRetour')), int, 0)
        # you can find api documentation on errors here : https://mabanque.bnpparibas/rsc/contrib/document/properties/identification-fr-part-V1.json
        if error:
            try:
                # this page can be unreachable
                error_page = self.browser.list_error_page.open()
                msg = error_page.get_error_message(error) or self.get('message')
            except ConnectionError:
                msg = self.get('message')

            wrongpass_codes = [201, 21510, 203, 202, 7]
            actionNeeded_codes = [21501, 3, 4, 50]
            # 'codeRetour' list
            # -1 : Erreur technique lors de l'accès à l'application
            # -99 : Service actuellement indisponible
            websiteUnavailable_codes = [207, 1000, 1001, -99, -1]
            if error in wrongpass_codes:
                raise BrowserIncorrectPassword(msg)
            elif error == 21:  # "Ce service est momentanément indisponible. Veuillez renouveler votre demande ultérieurement." -> In reality, account is blocked because of too much wrongpass
                raise ActionNeeded(u"Compte bloqué")
            elif error in actionNeeded_codes:
                raise ActionNeeded(msg)
            elif error in websiteUnavailable_codes:
                raise BrowserUnavailable(msg)
            else:
                raise AssertionError('Unexpected error at login: "%s" (code=%s)' % (msg, error))

        parser = html.HTMLParser()
        doc = html.parse(BytesIO(self.content), parser)
        error = CleanText('//div[h1[contains(text(), "Incident en cours")]]/p')(doc)
        if error:
            raise BrowserUnavailable(error)

    def login(self, username, password):
        url = '/identification-wspl-pres/grille/%s' % self.get('data.grille.idGrille')
        keyboard = self.browser.open(url)
        vk = BNPKeyboard(self.browser, keyboard)

        target = self.browser.BASEURL + 'SEEA-pa01/devServer/seeaserver'
        user_agent = self.browser.session.headers.get('User-Agent') or ''
        auth = self.render_template(
            self.get('data.authTemplate'),
            idTelematique=username,
            password=vk.get_string_code(password),
            clientele=user_agent
        )
        # XXX useless?
        csrf = self.generate_token()
        response = self.browser.location(target, data={'AUTH': auth, 'CSRF': csrf})

        if 'authentification-forte' in response.url:
            raise ActionNeeded("Veuillez réaliser l'authentification forte depuis votre navigateur.")
        if response.url.startswith('https://pro.mabanque.bnpparibas'):
            self.browser.switch('pro.mabanque')
        if response.url.startswith('https://banqueprivee.mabanque.bnpparibas'):
            self.browser.switch('banqueprivee.mabanque')


class OTPPage(HTMLPage):
    pass


class BNPPage(LoggedPage, JsonPage):
    def build_doc(self, text):
        try:
            return self.response.json(parse_float=Decimal)
        except ValueError:
            raise BrowserUnavailable()

    def on_load(self):
        code = cast(self.get('codeRetour'), int, 0)
        message = self.get('message', '')

        # -10 : "Utilisateur non authentifie"
        if code == -30 or (code == -10 and "non authentifie" in message):
            self.logger.debug('End of session detected, try to relog...')
            self.browser.do_login()
        elif code:
            self.logger.debug('Unexpected error: "%s" (code=%s)' % (self.get('message'), code))
            return (self.get('message'), code)


class ProfilePage(LoggedPage, JsonPage):
    ENCODING = 'utf-8'

    def get_error_message(self):
        return Dict('message')(self.doc)

    @method
    class get_profile(ItemElement):
        def condition(self):
            return Dict('codeRetour')(self) == '0'

        item_path = 'data/initialisation/informationsClient/'

        klass = Person

        def parse(self, el):
            if (
                not Dict(self.item_path + 'etatCivil/prenom')(el).strip()
                and not Dict(self.item_path + 'etatCivil/nom')(el).strip()
            ):
                raise ProfileMissing()

        obj_name = Format('%s %s', Dict(item_path + 'etatCivil/prenom'), Dict(item_path + 'etatCivil/nom'))
        obj_spouse_name = Dict(item_path + 'etatCivil/nomMarital', default=NotAvailable)
        obj_birth_date = Date(Dict(item_path + 'etatCivil/dateNaissance'), dayfirst=True)
        obj_nationality = Dict(item_path + 'etatCivil/nationnalite')
        obj_phone = Dict(item_path + 'etatCivil/numMobile')
        obj_email = Dict(item_path + 'etatCivil/mail')
        obj_job = Dict(item_path + 'situationPro/activiteExercee')
        obj_job_start_date = Date(Dict(item_path + 'situationPro/dateDebut'), dayfirst=True, default=NotAvailable)
        obj_company_name = Dict(item_path + 'situationPro/nomEmployeur')

        def obj_company_siren(self):
            siren = Dict('data/initialisation/informationsClient/monEntreprise/siren')(self.page.doc)
            return siren or NotAvailable


class AccountsPage(BNPPage):
    def get_user_ikpi(self):
        return self.doc['data']['infoUdc']['titulaireConnecte']['ikpi']

    @method
    class iter_accounts(DictElement):
        item_xpath = 'data/infoUdc/familleCompte'

        class iter_accounts_details(DictElement):
            item_xpath = 'compte'

            class item(ItemElement):
                def validate(self, obj):
                    # We skip loans with a balance of 0 because the JSON returned gives
                    # us no info (only `null` values on all fields), so there is nothing
                    # useful to display
                    return obj.type != Account.TYPE_LOAN or obj.balance != 0

                FAMILY_TO_TYPE = {
                    1: Account.TYPE_CHECKING,
                    2: Account.TYPE_SAVINGS,
                    3: Account.TYPE_DEPOSIT,
                    4: Account.TYPE_MARKET,
                    5: Account.TYPE_LIFE_INSURANCE,
                    6: Account.TYPE_LIFE_INSURANCE,
                    8: Account.TYPE_LOAN,
                    9: Account.TYPE_LOAN,
                }

                LABEL_TO_TYPE = {
                    'PEA Espèces': Account.TYPE_PEA,
                    'PEA PME Espèces': Account.TYPE_PEA,
                    'PEA Titres': Account.TYPE_PEA,
                    'PEL': Account.TYPE_SAVINGS,
                    'BNPP MP PERP': Account.TYPE_PERP,
                    'Plan Epargne Retraite Particulier': Account.TYPE_PERP,
                    'Crédit immobilier': Account.TYPE_MORTGAGE,
                    'Réserve Provisio': Account.TYPE_REVOLVING_CREDIT,
                    'Prêt personnel': Account.TYPE_CONSUMER_CREDIT,
                    'Crédit Silo': Account.TYPE_REVOLVING_CREDIT,
                }

                klass = Account

                obj_id = Dict('key')
                obj_label = Coalesce(
                    Dict('libellePersoProduit', default=NotAvailable),
                    Dict('libelleProduit', default=NotAvailable),
                    default=NotAvailable
                )
                obj_currency = Currency(Dict('devise'))
                obj_type = Coalesce(
                    Map(Dict('libelleProduit'), LABEL_TO_TYPE, default=NotAvailable),
                    Map(Env('account_type'), FAMILY_TO_TYPE, default=NotAvailable),
                    default=Account.TYPE_UNKNOWN
                )
                obj_balance = Dict('soldeDispo')
                obj_coming = Dict('soldeAVenir')
                obj_number = Dict('value')
                obj__subscriber = Format('%s %s', Dict('titulaire/nom'), Dict('titulaire/prenom'))
                obj__iduser = Dict('titulaire/ikpi')

                def obj_iban(self):
                    iban = Map(Dict('key'), Env('ibans')(self), default=NotAvailable)(self)

                    if not empty(iban):
                        if not is_iban_valid(iban):
                            iban = rib2iban(rebuild_rib(iban))
                        return iban
                    return None

                def obj_ownership(self):
                    indic = Dict('titulaire/indicTitulaireCollectif', default=None)(self)
                    # The boolean is in the form of a string ('true' or 'false')
                    if indic == 'true':
                        return AccountOwnership.CO_OWNER
                    elif indic == 'false':
                        if self.page.get_user_ikpi() == Dict('titulaire/ikpi')(self):
                            return AccountOwnership.OWNER
                        return AccountOwnership.ATTORNEY
                    return NotAvailable

                # softcap not used TODO don't pass this key when backend is ready
                # deferred cb can disappear the day after the appear, so 0 as day_for_softcap
                obj__bisoftcap = {'deferred_cb': {'softcap_day': 1000, 'day_for_softcap': 1}}

            def parse(self, el):
                self.env['account_type'] = Dict('idFamilleCompte')(el)


class LoanDetailsPage(BNPPage):
    @method
    class fill_loan_details(ItemElement):
        def condition(self):
            # If the loan doesn't have any info (that means the loan is already refund),
            # the data/message is null whereas it is set to "OK" when everything is fine.
            return Dict('data/message')(self) == 'OK'

        obj_total_amount = Dict('data/montantPret')
        obj_maturity_date = Date(Dict('data/dateEcheanceRemboursement'), dayfirst=True)
        obj_duration = Dict('data/dureeRemboursement')
        obj_rate = Dict('data/tauxRemboursement')
        obj_nb_payments_left = Dict('data/nbRemboursementRestant')
        obj_next_payment_date = Date(Dict('data/dateProchainAmortissement'), dayfirst=True)
        obj__subscriber = Format('%s %s', Dict('data/titulaire/nom'), Dict('data/titulaire/prenom'))
        obj__iduser = None

    @method
    class fill_revolving_details(ItemElement):
        obj_total_amount = Dict('data/montantDisponible')
        obj_rate = Dict('data/tauxInterets')
        obj__subscriber = Format('%s %s', Dict('data/titulaire/nom'), Dict('data/titulaire/prenom'))
        obj__iduser = None


class AccountsIBANPage(BNPPage):
    def get_ibans_dict(self):
        return dict([(a['ibanCrypte'], a['iban']) for a in self.path('data.listeRib.*.infoCompte')])


class MyRecipient(ItemElement):
    klass = Recipient

    def obj_currency(self):
        return Dict('devise')(self) or NotAvailable

    def validate(self, el):
        # For the moment, we skip this kind of recipient:
        # {"nomBeneficiaire":"Aircraft Guaranty Holdings LLC","idBeneficiaire":"00002##00002##FRSTUS44XXX##130018430","ibanNumCompte":"130018430","typeIban":"0","bic":"FRSTUS44XXX","statut":"1","numListe":"00002","typeBeneficiaire":"INTER","devise":"USD","tauxConversion":"1.047764","nbDecimale":"2","typeFrais":"","adresseBeneficiaire":"","nomBanque":"Frost National Bank","adresseBanque":"100 West Houston Street San Antonio, Texas 78205 USA ","canalActivation":"1","libelleStatut":"Activé"}
        return is_iban_valid(el.iban)


class TransferInitPage(BNPPage):
    def on_load(self):
        message_code = BNPPage.on_load(self)
        if message_code is not None:
            raise TransferAssertionError('%s, code=%s' % (message_code[0], message_code[1]))

    def get_ibans_dict(self, account_type):
        return dict([(a['ibanCrypte'], a['iban'])
                     for a in self.path('data.infoVirement.listeComptes%s.*' % account_type)])

    def can_transfer_to_recipients(self, origin_account_id):
        return next(
            a['eligibleVersBenef']
            for a in self.path('data.infoVirement.listeComptesDebiteur.*')
            if a['ibanCrypte'] == origin_account_id
        ) == '1'

    @method
    class transferable_on(DictElement):
        item_xpath = 'data/infoVirement/listeComptesCrediteur'

        class item(MyRecipient):
            def condition(self):
                return Dict('ibanCrypte')(self.el) != self.env['origin_account_ibancrypte']

            obj_id = Dict('ibanCrypte')
            obj_label = Dict('libelleCompte')
            obj_iban = Dict('iban')
            obj_category = u'Interne'
            obj__web_state = None

            def obj_bank_name(self):
                return u'BNP PARIBAS'

            def obj_enabled_at(self):
                return datetime.now().replace(microsecond=0)

    @method
    class iter_emitters(DictElement):
        item_xpath = 'data/infoVirement/listeComptesDebiteur'

        class item(ItemElement):
            klass = Emitter

            obj_id = Dict('ibanCrypte')
            obj_label = Dict('libelleCompte')
            obj_currency = Dict('devise')
            obj_number_type = EmitterNumberType.IBAN
            obj_number = Dict('iban')
            obj_balance = Dict('solde')


class RecipientsPage(BNPPage):
    @method
    class iter_recipients(DictElement):
        item_xpath = 'data/infoBeneficiaire/listeBeneficiaire'
        # We ignore duplicate because BNP allows differents recipients with the same iban
        ignore_duplicate = True

        class item(MyRecipient):
            # For the moment, only yield ready to transfer on recipients.
            def condition(self):
                return Dict('libelleStatut')(self.el) in [u'Activé', u'Temporisé', u'En attente']

            obj_id = obj_iban = Dict('ibanNumCompte')
            obj__transfer_id = Dict('idBeneficiaire')
            obj_label = Dict('nomBeneficiaire')
            obj_category = u'Externe'
            obj__web_state = Dict('libelleStatut')

            def obj_bank_name(self):
                return Dict('nomBanque')(self) or NotAvailable

            def obj_enabled_at(self):
                if Dict('libelleStatut')(self) == u'Activé':
                    return datetime.now().replace(microsecond=0)
                return (datetime.now() + timedelta(days=1)).replace(microsecond=0)

    def has_digital_key(self):
        return (
            Dict('data/infoBeneficiaire/authentForte')(self.doc)
            and Dict('data/infoBeneficiaire/nomDeviceAF', default=False)(self.doc)
        )


class ValidateTransferPage(BNPPage):
    def check_errors(self):
        if 'data' not in self.doc:
            raise TransferBankError(message=self.doc['message'])

    def abort_if_unknown(self, transfer_data):
        try:
            assert transfer_data['typeOperation'] in ['1', '2'], 'Transfer operation type is %s' % transfer_data['typeOperation']
            assert transfer_data['repartitionFrais'] == '0', 'Transfer fees is not 0'
            assert transfer_data['devise'] == 'EUR', "Transfer currency is not EUR, it's %s" % transfer_data['devise']
            assert not transfer_data['montantDeviseEtrangere'], 'Transfer currency is foreign currency'
        except AssertionError as e:
            raise TransferAssertionError(e)

    def handle_response(self, account, recipient, amount, reason):
        self.check_errors()
        transfer_data = self.doc['data']['validationVirement']

        self.abort_if_unknown(transfer_data)

        if 'idBeneficiaire' in transfer_data and transfer_data['idBeneficiaire'] is not None:
            assert transfer_data['idBeneficiaire'] == recipient._transfer_id
        elif transfer_data.get('ibanCompteCrediteur'):
            assert transfer_data['ibanCompteCrediteur'] == recipient.iban

        transfer = Transfer()
        transfer.currency = transfer_data['devise']
        transfer.amount = Decimal(transfer_data['montantEuros'])
        transfer.account_iban = transfer_data['ibanCompteDebiteur']
        transfer.account_id = account.id
        try:
            transfer.recipient_iban = transfer_data['ibanCompteCrediteur'] or recipient.iban
        except KeyError:
            # In last version, json contains a key 'idBeneficiaire' containing:
            # "idBeneficiaire" : "00003##00001####FR7610278123456789028070101",
            transfer.recipient_id = transfer_data['idBeneficiaire']
            transfer.recipient_iban = transfer.recipient_id.split('#')[-1] or recipient.iban
        else:
            transfer.recipient_id = recipient.id
        transfer.exec_date = parse_french_date(transfer_data['dateExecution']).date()
        transfer.fees = Decimal(transfer_data.get('montantFrais', '0'))
        transfer.label = transfer_data['motifVirement']

        transfer.account_label = account.label
        transfer.recipient_label = recipient.label
        transfer.id = transfer_data['reference']
        # This is true if a transfer with the same metadata has already been done recently
        transfer._doublon = transfer_data['doublon']
        transfer.account_balance = account.balance

        return transfer


class RegisterTransferPage(ValidateTransferPage):
    def handle_response(self, transfer):
        self.check_errors()
        transfer_data = self.doc['data']['enregistrementVirement']

        transfer.id = transfer_data['reference']
        transfer.exec_date = parse_french_date(self.doc['data']['enregistrementVirement']['dateExecution']).date()
        # Timestamp at which the bank registered the transfer
        register_date = re.sub(' 24:', ' 00:', self.doc['data']['enregistrementVirement']['dateEnregistrement'])
        transfer._register_date = parse_french_date(register_date)

        return transfer


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(u'^(?P<category>CHEQUE)(?P<text>.*)'), FrenchTransaction.TYPE_CHECK),
        (
            re.compile(
                r'^(?P<category>FACTURE CARTE) DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<text>.*?)( CA?R?T?E? ?\d*X*\d*)?$'
            ),
            FrenchTransaction.TYPE_CARD,
        ),
        (re.compile('^(?P<category>(PRELEVEMENT|TELEREGLEMENT|TIP)) (?P<text>.*)'), FrenchTransaction.TYPE_ORDER),
        (
            re.compile(r'^(?P<category>PRLV( EUROPEEN)? SEPA) (?P<text>.*?)( MDT/.*?)?( ECH/\d+)?( ID .*)?$'),
            FrenchTransaction.TYPE_ORDER,
        ),
        (re.compile('^(?P<category>ECHEANCEPRET)(?P<text>.*)'), FrenchTransaction.TYPE_LOAN_PAYMENT),
        (
            re.compile(
                r'^(?P<category>RETRAIT DAB) ?((?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})( (?P<HH>\d+)H(?P<MM>\d+))?( \d+)? (?P<text>.*))?'
            ),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^(?P<category>VIR(EMEN)?T? (RECU |FAVEUR )?(TIERS )?)\w+ \d+/\d+ \d+H\d+ \w+ (?P<text>.*)$'),
            FrenchTransaction.TYPE_TRANSFER,
        ),
        (
            re.compile(
                '^(?P<category>VIR(EMEN)?T? (EUROPEEN )?(SEPA )?(RECU |FAVEUR |EMIS )?(TIERS )?)(/FRM |/DE |/MOTIF |/BEN )?(?P<text>.*?)(/.+)?$'
            ),
            FrenchTransaction.TYPE_TRANSFER,
        ),
        (
            re.compile(r'^(?P<category>REMBOURST) CB DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_PAYBACK,
        ),
        (
            re.compile(
                r'^(?P<category>(((1ER|(2|3)EME) TIERS)|INTERETS) SUR FACTURE) DE \d+,\d{2} EUR DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<text>.*)'
            ),
            FrenchTransaction.TYPE_CARD,
        ),
        (re.compile('^(?P<category>REMBOURST)(?P<text>.*)'), FrenchTransaction.TYPE_PAYBACK),
        (re.compile('^(?P<category>COMMISSIONS)(?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile('^(?P<text>(?P<category>REMUNERATION).*)'), FrenchTransaction.TYPE_BANK),
        (re.compile('^(?P<category>REMISE CHEQUES)(?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
    ]


class HistoryPage(BNPPage):
    CODE_TO_TYPE = {
        1: Transaction.TYPE_CHECK,  # Chèque émis
        2: Transaction.TYPE_CHECK,  # Chèque reçu
        3: Transaction.TYPE_CASH_DEPOSIT,  # Versement espèces
        4: Transaction.TYPE_ORDER,  # Virements reçus
        5: Transaction.TYPE_ORDER,  # Virements émis
        6: Transaction.TYPE_LOAN_PAYMENT,  # Prélèvements / amortissements prêts
        7: Transaction.TYPE_CARD,  # Paiements carte,
        8: Transaction.TYPE_CARD,  # Carte / Formule BNP Net,
        9: Transaction.TYPE_UNKNOWN,  # Opérations Titres
        10: Transaction.TYPE_UNKNOWN,  # Effets de Commerce
        11: Transaction.TYPE_WITHDRAWAL,  # Retraits d'espèces carte
        12: Transaction.TYPE_UNKNOWN,  # Opérations avec l'étranger
        13: Transaction.TYPE_CARD,  # Remises Carte
        14: Transaction.TYPE_WITHDRAWAL,  # Retraits guichets
        15: Transaction.TYPE_BANK,  # Intérêts/frais et commissions
        16: Transaction.TYPE_UNKNOWN,  # Tercéo
        30: Transaction.TYPE_UNKNOWN,  # Divers
    }

    COMING_TYPE_TO_TYPE = {
        2: Transaction.TYPE_ORDER,  # Prélèvement
        3: Transaction.TYPE_CHECK,  # Chèque
        4: Transaction.TYPE_CARD,  # Opération carte
    }

    def one(self, path, context=None):
        try:
            return list(self.path(path, context))[0]
        except IndexError:
            return None

    def iter_history(self):
        for op in self.get('data.listerOperations.compte.operationPassee') or []:
            codeFamille = cast(self.one('operationType.codeFamille', op), int)
            tr = Transaction.from_dict({
                'id': op.get('idOperation'),
                'type': self.CODE_TO_TYPE.get(codeFamille) or Transaction.TYPE_UNKNOWN,
                'category': op.get('categorie'),
                'amount': self.one('montant.montant', op),
            })
            tr.parse(
                raw=CleanText().filter(op.get('libelleOperation')),
                date=parse_french_date(op.get('dateOperation')),
                vdate=parse_french_date(self.one('montant.valueDate', op))
            )

            raw_is_summary = re.match(
                r'FACTURE CARTE SELON RELEVE DU\b|FACTURE CARTE CARTE AFFAIRES \d{4}X{8}\d{4} SUIVANT\b', tr.raw
            )
            if tr.type == Transaction.TYPE_CARD and raw_is_summary:
                tr.type = Transaction.TYPE_CARD_SUMMARY
                tr.deleted = True
            yield tr

    def iter_coming(self):
        for op in self.path('data.listerOperations.compte.operationAvenir.*.operation.*'):
            codeOperation = cast(op.get('codeOperation'), int, 0)
            # Coming transactions don't have real id
            tr = Transaction.from_dict({
                'type': self.COMING_TYPE_TO_TYPE.get(codeOperation) or Transaction.TYPE_UNKNOWN,
                'amount': op.get('montant'),
                'card': op.get('numeroPorteurCarte'),
            })

            tr.date = parse_french_date(op.get('dateOperation'))
            tr.vdate = parse_french_date(op.get('valueDate'))
            tr.rdate = NotAvailable
            tr.raw = CleanText().filter(op.get('libelle'))
            parse_with_patterns(tr.raw, tr, Transaction.PATTERNS)

            if tr.type == Transaction.TYPE_CARD:
                tr.type = self.browser.card_to_transaction_type.get(op.get('keyCarte'), Transaction.TYPE_DEFERRED_CARD)
            yield tr


class ListDetailCardPage(BNPPage):
    def get_card_to_transaction_type(self):
        d = {}
        for card in self.path('data.responseRestitutionCarte.listeCartes.*'):
            if 'DIFFERE' in card['typeDebit']:
                tr_type = Transaction.TYPE_DEFERRED_CARD
            else:
                tr_type = Transaction.TYPE_CARD
            d[card['numCarteCrypte']] = tr_type
        return d


class LifeInsurancesPage(BNPPage):
    investments_path = 'data.infosContrat.repartition.listeSupport.*'

    def iter_investments(self):
        for support in self.path(self.investments_path):
            inv = Investment()
            if 'codeIsin' in support:
                inv.code = inv.id = support['codeIsin']
                inv.quantity = support.get('nbUC', NotAvailable)
                inv.unitvalue = support.get('valUC', NotAvailable)

            inv.label = support['libelle']
            inv.valuation = support.get('montant', NotAvailable)
            inv.set_empty_fields(NotAvailable)
            yield inv


class LifeInsurancesHistoryPage(BNPPage):
    IGNORED_STATUSES = (
        'En cours',
        'Sans suite',
    )

    def iter_history(self, coming):
        for op in self.get('data.listerMouvements.listeMouvements') or []:
            # We have not date for this statut so we just skit it
            if op.get('statut') in self.IGNORED_STATUSES:
                continue

            tr = Transaction.from_dict({
                'type': Transaction.TYPE_BANK,
                '_state': op.get('statut'),
                'amount': op.get('montantNet'),
            })

            vdate = None
            if op.get('dateEffet'):
                vdate = parse_french_date(op.get('dateEffet'))

            tr.parse(
                date=parse_french_date(op.get('dateSaisie')),
                vdate=vdate,
                raw='%s %s' % (op.get('libelleMouvement'), op.get('canalSaisie') or '')
            )
            tr._op = op

            if not tr.amount:
                if op.get('rib', {}).get('codeBanque') == 'null':
                    self.logger.info('ignoring non-transaction with label %r', tr.raw)
                    continue

            if (op.get('statut') == u'Traité') ^ coming:
                yield tr


class LifeInsurancesDetailPage(LifeInsurancesPage):
    investments_path = 'data.detailMouvement.listeSupport.*'


class NatioVieProPage(BNPPage):
    # This form is required to go to the capitalisation contracts page.
    def get_params(self):
        params = {
            'app': 'BNPNET',
            'hageGroup': 'consultationBnpnet',
            'init': 'true',
            'multiInit': 'false',
        }
        params['a0'] = self.doc['data']['nationVieProInfos']['a0']
        # The number of "p" keys may vary (p0, p1, p2 ... up to p13 or more)
        for key, value in self.doc['data']['nationVieProInfos']['listeP'].items():
            params[key] = value
        # We must decode the values before constructing the URL:
        for k, v in params.items():
            params[k] = unquote_plus(v)
        return params


CAPITALISATION_TYPES = {
    'Multiplacements': Account.TYPE_LIFE_INSURANCE,
    'Multihorizons': Account.TYPE_LIFE_INSURANCE,
    'Libertéa Privilège': Account.TYPE_LIFE_INSURANCE,
    'Avenir Retraite': Account.TYPE_LIFE_INSURANCE,
    'Multiciel Privilège': Account.TYPE_CAPITALISATION,
    'Plan Epargne Retraite Particulier': Account.TYPE_PERP,
    "Plan d'Épargne Retraite des Particuliers": Account.TYPE_PERP,
    'PEP Assurvaleurs': Account.TYPE_DEPOSIT,
}


class CapitalisationPage(LoggedPage, PartialHTMLPage):
    def has_contracts(self):
        # This message will appear if the page "Assurance Vie" contains no contract.
        return not CleanText('//td[@class="message"]/text()[starts-with(., "Pour toute information")]')(self.doc)

    @method
    class iter_capitalisation(TableElement):
        # Other types of tables may appear on the page (such as Alternative Emprunteur/Capital Assuré)
        # But they do not contain bank accounts so we must avoid them.
        item_xpath = '//table/tr[preceding-sibling::tr[th[text()="Libellé du contrat"]]][td[@class="ligneTableau"]]'
        head_xpath = '//table/tr/th[@class="headerTableau"]'

        col_label = 'Libellé du contrat'
        col_id = 'Numéro de contrat'
        col_balance = 'Montant'
        col_currency = "Monnaie d'affichage"

        class item(ItemElement):
            klass = Account

            obj_label = CleanText(TableCell('label'))
            obj_id = CleanText(TableCell('id'))
            obj_number = CleanText(TableCell('id'))
            obj_balance = CleanDecimal(TableCell('balance'), replace_dots=True)
            obj_coming = None
            obj_iban = None
            obj__subscriber = None
            obj__iduser = None
            obj_type = MapIn(Field('label'), CAPITALISATION_TYPES, Account.TYPE_UNKNOWN)

            def obj_currency(self):
                currency = CleanText(TableCell('currency')(self))(self)
                return Account.get_currency(currency)

            # Required to get the investments of each "Assurances Vie" account:
            def obj__details(self):
                raw_details = CleanText((TableCell('balance')(self)[0]).xpath('./a/@href'))(self)
                m = re.search(r"Window\('(.*?)',window", raw_details)
                if m:
                    return m.group(1)

    def get_params(self, account):
        form = self.get_form(xpath='//form[@name="formListeContrats"]')
        form['postValue'] = account._details
        return form

    # The investments vdate is out of the investments table and is the same for all investments:
    def get_vdate(self):
        return parse_french_date(
            CleanText('//table[tr[th[text()[contains(., "Date de valorisation")]]]]/tr[2]/td[2]')(self.doc)
        )

    @method
    class iter_investments(TableElement):
        # Investment lines contain at least 5 <td> tags
        item_xpath = '//table[tr[th[text()[contains(., "Libellé")]]]]/tr[count(td)>=5]'
        head_xpath = '//table[tr[th[text()[contains(., "Libellé")]]]]/tr/th[@class="headerTableau"]'

        col_label = 'Libellé'
        col_code = 'Code ISIN'
        col_quantity = 'Nombre de parts'
        col_valuation = 'Montant'
        col_portfolio_share = 'Montant en %'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_valuation = CleanDecimal(TableCell('valuation'), replace_dots=True)
            obj_portfolio_share = Eval(
                lambda x: x / 100,
                CleanDecimal(TableCell('portfolio_share'), replace_dots=True)
            )

            # There is no "unitvalue" information available on the "Assurances Vie" space.

            def obj_quantity(self):
                quantity = TableCell('quantity')(self)
                if CleanText(quantity)(self) == '-':
                    return NotAvailable
                return CleanDecimal(quantity, replace_dots=True)(self)

            def obj_code(self):
                isin = CleanText(TableCell('code')(self))(self)
                return isin or NotAvailable

            def obj_code_type(self):
                if is_isin_valid(Field('code')(self)):
                    return Investment.CODE_TYPE_ISIN
                return NotAvailable

            def obj_vdate(self):
                return self.page.get_vdate()


class MarketListPage(BNPPage):
    def get_list(self):
        return self.get('securityAccountsList') or []


class MarketSynPage(BNPPage):
    def get_list(self):
        return self.get('synSecurityAccounts') or []


class MarketPage(BNPPage):
    investments_path = 'listofPortfolios.*'

    def iter_investments(self):
        for support in self.path(self.investments_path):
            inv = Investment()
            inv.id = support['securityCode']
            if is_isin_valid(support['securityCode']):
                inv.code = support['securityCode']
                inv.code_type = Investment.CODE_TYPE_ISIN
            else:
                inv.code = inv.code_type = NotAvailable
            inv.quantity = support['quantityOwned']
            inv.unitvalue = support['currentQuote']
            inv.unitprice = support['averagePrice']
            inv.label = support['securityName']
            inv.valuation = support['valorizationValuation']
            inv.diff = support['profitLossValorisation']
            inv.set_empty_fields(NotAvailable)
            yield inv


class MarketHistoryPage(BNPPage):
    def iter_history(self):
        for op in self.get('contentList') or []:

            tr = Transaction.from_dict({
                'type': Transaction.TYPE_BANK,
                'amount': op.get('movementAmount'),
                'date': datetime.fromtimestamp(op.get('movementDate') / 1000),
                'label': op.get('operationName'),
            })

            tr.investments = []
            inv = Investment()
            if is_isin_valid(op.get('securityCode')):
                inv.code = op.get('securityCode')
                inv.code_type = Investment.CODE_TYPE_ISIN
            else:
                inv.code = inv.code_type = NotAvailable
            inv.quantity = op.get('movementQuantity')
            inv.label = op.get('securityName')
            inv.set_empty_fields(NotAvailable)
            tr.investments.append(inv)
            yield tr


MARKET_ORDER_DIRECTIONS = {
    'Achat': MarketOrderDirection.BUY,
    'Vente': MarketOrderDirection.SALE,
}


class MarketOrdersPage(BNPPage):
    @method
    class iter_market_orders(DictElement):
        item_xpath = 'contentList'

        class item(ItemElement):
            klass = MarketOrder

            # Note: there is no information on the order type
            obj_id = CleanText(Dict('orderReference'))
            obj_label = CleanText(Dict('securityName'))
            obj_state = CleanText(Dict('orderStatusLabel'))
            obj_code = IsinCode(CleanText(Dict('securityCode')), default=NotAvailable)
            obj_stock_market = CleanText(Dict('stockExchangeName'))
            obj_date = FromTimestamp(
                Eval(lambda t: t / 1000, Dict('orderDateTransmission'))
            )
            obj_direction = Map(
                CleanText(Dict('orderNatureLabel')),
                MARKET_ORDER_DIRECTIONS,
                MarketOrderDirection.UNKNOWN
            )

            def obj_quantity(self):
                if empty(Dict('quantity')(self)):
                    return NotAvailable
                return Decimal(str(Dict('quantity')(self)))

            def obj_unitprice(self):
                if empty(Dict('executionPrice')(self)):
                    return NotAvailable
                return Decimal(str(Dict('executionPrice')(self)))

            def obj_ordervalue(self):
                if empty(Dict('limitPrice')(self)):
                    return NotAvailable
                return Decimal(str(Dict('limitPrice')(self)))

            def obj_currency(self):
                # Most of the times the currency is set to null
                if empty(Dict('orderCurrency')(self)):
                    return NotAvailable
                return Currency(Dict('orderCurrency'), default=NotAvailable)(self)


class AdvisorPage(BNPPage):
    def has_error(self):
        return (self.doc.get('message') == 'Erreur technique')

    @method
    class get_advisor(ListElement):
        class item(ItemElement):
            klass = Advisor

            obj_name = Format('%s %s %s', Dict('data/civilite'), Dict('data/prenom'), Dict('data/nom'))
            obj_email = Regexp(Dict('data/mail'), r'(?=\w)(.*)', default=NotAvailable)
            obj_phone = CleanText(Dict('data/telephone'), replace=[(' ', '')])
            obj_mobile = CleanText(Dict('data/mobile'), replace=[(' ', '')])
            obj_fax = CleanText(Dict('data/fax'), replace=[(' ', '')])
            obj_agency = Dict('data/agence')
            obj_address = Format(
                '%s %s %s', Dict('data/adresseAgence'), Dict('data/codePostalAgence'), Dict('data/villeAgence')
            )


class AddRecipPage(BNPPage):
    def on_load(self):
        code = cast(self.get('codeRetour'), int)
        if code:
            raise AddRecipientBankError(message=self.get('message'))

    def get_recipient(self, recipient):
        # handle polling response
        r = Recipient()
        r.iban = recipient.iban
        r.id = self.get('data.gestionBeneficiaire.identifiantBeneficiaire')
        r.label = recipient.label
        r.category = u'Externe'
        r.enabled_at = datetime.now().replace(microsecond=0)
        r.currency = u'EUR'
        r.bank_name = NotAvailable
        r._id_transaction = self.get('data.gestionBeneficiaire.idTransactionAF') or NotAvailable
        r._transfer_id = self.get('data.gestionBeneficiaire.identifiantBeneficiaire') or NotAvailable
        return r


class ActivateRecipPage(AddRecipPage):
    def is_recipient_validated(self):
        authent_state = self.doc['data']['verifAuthentForte']['authentForteDone']
        # 0: recipient is in validating state, continue polling
        # 1: recipient is validated
        # 2: user has cancelled
        # 3: operation timeout
        assert authent_state in (0, 1, 2, 3), 'State of authent is %s' % authent_state
        if authent_state == 2:
            raise AppValidationCancelled(message="La demande d'ajout de bénéficiaire a été annulée.")
        elif authent_state == 3:
            raise AppValidationExpired()
        return authent_state

    def get_recipient(self, recipient):
        r = Recipient()
        r.iban = recipient.iban
        r.id = recipient.id
        r.label = recipient.label
        r.category = u'Externe'
        r.enabled_at = datetime.now().replace(microsecond=0) + timedelta(days=1)
        r.currency = u'EUR'
        r.bank_name = self.get('data.activationBeneficiaire.nomBanque')
        return r


class UselessPage(LoggedPage, HTMLPage):
    pass


class TransfersPage(BNPPage):
    @method
    class iter_transfers(DictElement):
        item_xpath = 'data/historiqueVirement/virements'

        class item(ItemElement):
            # TODO handle periodic transfer, it was not working during the development of this part...
            klass = Transfer

            obj_id = Dict('idVirement')

            # when a transfer is canceled, dateStatut seems to be set to the cancel date
            obj_exec_date = Date(
                Dict('dateStatut'),
                dayfirst=True,
            )
            obj_creation_date = Date(
                Dict('dateSaisie'),
                dayfirst=True,
            )
            obj_label = Dict('motif')
            obj_recipient_iban = Dict('compteCredite')
            obj_account_iban = Dict('compteDebite')
            obj_recipient_label = Dict('libelleCompteCredite')
            # already saw the case when this field did was not here. may be the emitter account did not exist anymore?
            obj_account_label = Dict('libelleCompteDebite', default=NotAvailable)

            STATUSES = {
                '4': TransferStatus.DONE,
                '3': TransferStatus.SCHEDULED,
                '1': TransferStatus.CANCELLED,
            }
            obj_status = Map(Dict('statut'), STATUSES)
            obj_amount = CleanDecimal.US(Dict('montant'))
            obj_currency = Dict('devise')

            def obj_date_type(self):
                # since periodic transfer does not work (tried on the app and the website)
                # this type is not handled yet
                # TODO handle periodic, for now it will be defaulted to FIRST_OPEN_DAY
                if Dict('ip')(self):
                    return TransferDateType.INSTANT
                if not Dict('immediat')(self):
                    return TransferDateType.DEFERRED
                return TransferDateType.FIRST_OPEN_DAY
