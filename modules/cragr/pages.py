# Copyright(C) 2023 Powens
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

import json
import re
from decimal import Decimal
from urllib.parse import urljoin

import dateutil

from woob.browser.elements import DictElement, ItemElement, ListElement, method
from woob.browser.filters.html import Attr, Link
from woob.browser.filters.javascript import JSVar
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanDecimal, CleanText, Coalesce
from woob.browser.filters.standard import Currency as CleanCurrency
from woob.browser.filters.standard import Date, Env, Eval, Field, Format, Lower, Map, MapIn, Regexp, Upper
from woob.browser.pages import HTMLPage, JsonPage, LoggedPage
from woob.capabilities import NotAvailable
from woob.capabilities.bank import Account, AccountOwnership, AccountOwnerType
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import empty
from woob.capabilities.contact import Advisor
from woob.capabilities.profile import Company, Person
from woob.exceptions import ActionNeeded, BrowserPasswordExpired, ParseError
from woob.tools.capabilities.bank.investments import IsinCode, IsinType, is_isin_valid
from woob.tools.capabilities.bank.transactions import FrenchTransaction


ACCOUNT_OWNERSHIPS = {
    "TITULAIRE": AccountOwnership.OWNER,
    "COTITULAIRE": AccountOwnership.CO_OWNER,
    "REPRESENTANT_LEGAL": AccountOwnership.ATTORNEY,
    "MANDATAIRE": AccountOwnership.ATTORNEY,
}


def float_to_decimal(f):
    return Decimal(str(f))


class Transaction(FrenchTransaction):
    # this is only used to to find the rdate
    PATTERNS = [
        (re.compile(r"^(?P<category>PAIEMENT PAR CARTE) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})$"), None),
        (re.compile(r"^(?P<category>PRELEVEMENT) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) .*"), None),
        (
            re.compile(
                r"^(?P<category>PRELEVEMENT) (?P<text>.*)(?<!\W\d{4}) (?P<dd>\d{2})\s(?P<mm>\d{2})\s(?P<yy>\d{4})(?:$|\s.*)"
            ),
            None,
        ),
        (
            re.compile(
                r"^(?P<category>VIREMENT EN VOTRE FAVEUR) (?P<text>.*) (?P<dd>3[01]|[12][0-9]|0[1-9])\.(?P<mm>1[0-2]|0[1-9])\.(?P<yy>\d{4})$"
            ),
            None,
        ),
        (
            re.compile(
                r"^(?P<category>REMBOURSEMENT DE PRET) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2,4})$",
            ),
            None,
        ),
        (re.compile(r"^(?P<category>RETRAIT AU DISTRIBUTEUR) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2}) .*"), None),
        (
            re.compile(
                r"^(?P<category>PRELEVEMENT URSSAF) (?P<text>.*) (du)? (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2,4})$"
            ),
            None,
        ),
        (
            re.compile(r"^(?P<category>VERSEMENT D'ESPECES) (?P<text>.*) (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) .*"),
            None,
        ),
        (re.compile(r"^(?P<category>PRELEVEMENT) (?P<text>.*) (?P<dd>\d{2})-(?P<mm>0[1-9]|1[012])$"), None),
        (re.compile(r"^(?P<text>(?P<category>AVOIR) .*) (?P<dd>\d{2})/(?P<mm>\d{2})$"), None),
    ]


class KeypadPage(JsonPage):
    def build_password(self, password):
        # Fake Virtual Keyboard: just get the positions of each digit.
        key_positions = [i for i in Dict("keyLayout")(self.doc)]
        return str(",".join([str(key_positions.index(i)) for i in password]))

    def get_keypad_id(self):
        return Dict("keypadId")(self.doc)


class LoginPage(HTMLPage):
    def get_login_form(self, username, keypad_password, keypad_id):
        form = self.get_form(id="loginForm")
        form["j_username"] = username[:11]
        form["j_password"] = keypad_password
        form["keypadId"] = keypad_id
        return form


class LoggedOutPage(HTMLPage):
    def is_here(self):
        return self.doc.xpath('//b[text()="FIN DE CONNEXION"]')


class FirstConnectionPage(LoggedPage, HTMLPage):
    def on_load(self):
        message = CleanText('//p[contains(text(), "votre première visite")]')(self.doc)
        if message:
            raise ActionNeeded(message)


class SecurityPage(JsonPage):
    def get_accounts_url(self):
        return Dict("url")(self.doc)


class TokenPage(LoggedPage, JsonPage):
    def get_token(self):
        return Dict("token")(self.doc)


class ChangePasswordPage(HTMLPage):
    def on_load(self):
        # Handle <p class="h1">Modifier mon code personnel&nbsp;</p>
        # Handle <h1><span class="h1">Modifier&nbsp;votre code personnel</span></h1>
        msg = CleanText('//*[@class="h1" and contains(text(), "code personnel")]')(self.doc)
        if msg:
            raise BrowserPasswordExpired(msg)


class UpdateProfilePage(HTMLPage):
    def get_action_message(self):
        return CleanText('//div[@class="description"]')(self.doc)


class TaxResidencyFillingPage(HTMLPage):
    def get_action_needed_message(self):
        return CleanText('//div[@class="warning"]/following-sibling::h1/div')(self.doc)


class ContractsPage(LoggedPage, HTMLPage):
    pass


ACCOUNT_TYPES = {
    "V O E CAPI": Account.TYPE_CAPITALISATION,
    "ESPGESTCAP": Account.TYPE_CAPITALISATION,
    "CCHQ": Account.TYPE_CHECKING,  # par
    "CCOU": Account.TYPE_CHECKING,  # pro
    "AUTO ENTRP": Account.TYPE_CHECKING,  # pro
    "DEVISE USD": Account.TYPE_CHECKING,
    "EKO": Account.TYPE_CHECKING,
    "MAJPROTEGE": Account.TYPE_CHECKING,  # Compte majeur protégé
    "LFDJ": Account.TYPE_CHECKING,  # Compte de mandataire LFDJ
    "PMU": Account.TYPE_CHECKING,  # Compte PMU S.N.C. TISM
    "DAV NANTI": Account.TYPE_SAVINGS,
    "LIV A": Account.TYPE_SAVINGS,
    "LIV A ASS": Account.TYPE_SAVINGS,
    "LDD": Account.TYPE_SAVINGS,
    "PEL": Account.TYPE_SAVINGS,
    "CEL": Account.TYPE_SAVINGS,
    "CEL2": Account.TYPE_SAVINGS,
    "CODEBIS": Account.TYPE_SAVINGS,
    "LJMO": Account.TYPE_SAVINGS,
    "CSL": Account.TYPE_SAVINGS,
    "LEP": Account.TYPE_SAVINGS,
    "LEF": Account.TYPE_SAVINGS,
    "TIWI": Account.TYPE_SAVINGS,
    "CSL LSO": Account.TYPE_SAVINGS,
    "CSL CSP": Account.TYPE_SAVINGS,
    "DAV TIGERE": Account.TYPE_SAVINGS,
    "CPTEXCPRO": Account.TYPE_SAVINGS,
    "CPTEXCPRO2": Account.TYPE_SAVINGS,
    "CPTEXCENT": Account.TYPE_SAVINGS,
    "CPTEXCAGRI": Account.TYPE_SAVINGS,  # Compte Excédent Agriculture
    "CPTDAV": Account.TYPE_SAVINGS,
    "ORCH": Account.TYPE_SAVINGS,  # Orchestra / PEP
    "CB": Account.TYPE_SAVINGS,  # Carré bleu / PEL
    "LIS": Account.TYPE_SAVINGS,
    "BOOSTE3": Account.TYPE_SAVINGS,
    "BOOSTE4": Account.TYPE_SAVINGS,  # Livret d'Epargne Forteo
    "PEPS": Account.TYPE_SAVINGS,  # Plan d'Epargne Populaire
    "LIPROJAGRI": Account.TYPE_SAVINGS,
    "épargne disponible": Account.TYPE_SAVINGS,
    "LTA": Account.TYPE_SAVINGS,  # Livret Tandem
    "DAT": Account.TYPE_DEPOSIT,
    "DAT5": Account.TYPE_DEPOSIT,
    "DPA": Account.TYPE_DEPOSIT,
    "DATG": Account.TYPE_DEPOSIT,
    "DATX": Account.TYPE_DEPOSIT,
    "CEA": Account.TYPE_DEPOSIT,  # Dépôt à terme
    "VAR": Account.TYPE_DEPOSIT,  # Dépôt à terme
    "épargne à terme": Account.TYPE_DEPOSIT,
    "PRET PERSO": Account.TYPE_LOAN,
    "P. ENTREPR": Account.TYPE_LOAN,
    "P. HABITAT": Account.TYPE_MORTGAGE,
    "P. CONV.": Account.TYPE_LOAN,
    "PRET 0%": Account.TYPE_LOAN,
    "INV PRO": Account.TYPE_LOAN,
    "TRES. PRO": Account.TYPE_LOAN,
    "CT ATT HAB": Account.TYPE_LOAN,
    "PRET CEL": Account.TYPE_LOAN,
    "PRET PEL": Account.TYPE_LOAN,
    "COLL. PUB": Account.TYPE_LOAN,
    "P.ENTREPR.": Account.TYPE_LOAN,
    "PEA": Account.TYPE_PEA,
    "PEAP": Account.TYPE_PEA,
    "DAV PEA": Account.TYPE_PEA,
    "PEA VPRIVI": Account.TYPE_PEA,
    "PEA VPATRI": Account.TYPE_PEA,
    "CPS": Account.TYPE_MARKET,
    "TITR": Account.TYPE_MARKET,
    "TITR CTD": Account.TYPE_MARKET,
    "ESPE INTEG": Account.TYPE_MARKET,
    "PVERT VITA": Account.TYPE_PERP,
    "réserves de crédit": Account.TYPE_CHECKING,
    "prêts personnels": Account.TYPE_LOAN,
    "crédits immobiliers": Account.TYPE_MORTGAGE,
    "ESC COM.": Account.TYPE_LOAN,
    "LIM TRESO": Account.TYPE_LOAN,
    "P.ETUDIANT": Account.TYPE_LOAN,
    "P. ACC.SOC": Account.TYPE_LOAN,
    "CAU. BANC.": Account.TYPE_LOAN,
    "CSAN": Account.TYPE_LOAN,
    "P SPE MOD": Account.TYPE_LOAN,
    "MT AUTRE": Account.TYPE_LOAN,  # Prêt d'investissement professionnel
    "PRET REAM.": Account.TYPE_LOAN,
    "épargne boursière": Account.TYPE_MARKET,
    "assurance vie et capitalisation": Account.TYPE_LIFE_INSURANCE,
    "PRED": Account.TYPE_LIFE_INSURANCE,
    "PREDI9 S2": Account.TYPE_LIFE_INSURANCE,
    "V.AVENIR": Account.TYPE_LIFE_INSURANCE,
    "FLORIA": Account.TYPE_LIFE_INSURANCE,
    "FLOR": Account.TYPE_LIFE_INSURANCE,
    "CAP DECOUV": Account.TYPE_LIFE_INSURANCE,
    "ESP LIB 2": Account.TYPE_LIFE_INSURANCE,
    "AUTRO": Account.TYPE_LIFE_INSURANCE,  # Autre Contrats Rothschild
    "OPPER": Account.TYPE_LIFE_INSURANCE,  # Open Perspective
    "OPEN STRAT": Account.TYPE_LIFE_INSURANCE,  # Open Strategie
    "ESPACELIB3": Account.TYPE_LIFE_INSURANCE,  # Espace Liberté 3
    "ESPACE LIB": Account.TYPE_LIFE_INSURANCE,  # Espace Liberté
    "ESPA. SELE": Account.TYPE_LIFE_INSURANCE,
    "ASS OPPORT": Account.TYPE_LIFE_INSURANCE,  # Assurance fonds opportunité
    "FLORIPRO": Account.TYPE_LIFE_INSURANCE,
    "FLORIANE 2": Account.TYPE_LIFE_INSURANCE,
    "FLORIAGRI": Account.TYPE_LIFE_INSURANCE,
    "AST SELEC": Account.TYPE_LIFE_INSURANCE,
    "PRGE": Account.TYPE_LIFE_INSURANCE,
    "CONF": Account.TYPE_LIFE_INSURANCE,
    "V O E": Account.TYPE_LIFE_INSURANCE,  # Vendome Optimum Euro
    "VENDOME": Account.TYPE_LIFE_INSURANCE,  # Vendome Optimum Euro
    "ESPGESTION": Account.TYPE_LIFE_INSURANCE,  # Espace Gestion
    "ESPGESTPEP": Account.TYPE_LIFE_INSURANCE,  # Espace Gestion PEP
    "OPTA": Account.TYPE_LIFE_INSURANCE,  # Optalissime
    "RENV VITAL": Account.TYPE_LIFE_INSURANCE,  # Rente viagère Vitalité
    "ANAE": Account.TYPE_LIFE_INSURANCE,
    "PAT STH": Account.TYPE_LIFE_INSURANCE,  # Patrimoine ST Honoré
    "PRSH2": Account.TYPE_LIFE_INSURANCE,  # Prestige ST Honoré 2
    "PSH3C": Account.TYPE_LIFE_INSURANCE,  # Prestige ST Honoré 3
    "CTT SOLID": Account.TYPE_LIFE_INSURANCE,  # predica
    "PARAF": Account.TYPE_LIFE_INSURANCE,  # bgpi Paraphe
    "PSHO3": Account.TYPE_LIFE_INSURANCE,  # Prestige ST Honoré 3
    "ASTERINNOV": Account.TYPE_LIFE_INSURANCE,  # Aster Innovation
    "AST EXCAP": Account.TYPE_CAPITALISATION,  # Excellence 2 Capitalisation
    "AST EXC2": Account.TYPE_LIFE_INSURANCE,  # bgpi Aster excellence 2
    "ACOR": Account.TYPE_LIFE_INSURANCE,  # Predica ACOR
    "PACA": Account.TYPE_CONSUMER_CREDIT,  # 'PAC' = 'Prêt à consommer'
    "PACC": Account.TYPE_CONSUMER_CREDIT,
    "PACP": Account.TYPE_CONSUMER_CREDIT,
    "PACR": Account.TYPE_CONSUMER_CREDIT,
    "PACV": Account.TYPE_CONSUMER_CREDIT,
    "PAC2": Account.TYPE_CONSUMER_CREDIT,
    "SUPPLETIS": Account.TYPE_REVOLVING_CREDIT,
    "OPEN": Account.TYPE_REVOLVING_CREDIT,
    "ATOUT LIB": Account.TYPE_REVOLVING_CREDIT,
    "PAGR": Account.TYPE_MADELIN,
    "ACCOR MULT": Account.TYPE_MADELIN,
    "PERASSUR": Account.TYPE_PER,
    "PERBANCGP": Account.TYPE_PER,
    "DAVPERBANC": Account.TYPE_PER,
    "PERBANCGL": Account.TYPE_PER,
    "ESPSELEC 2": Account.TYPE_LIFE_INSURANCE,
}

ACCOUNT_IS_LIQUIDITY = re.compile("compte especes? pea" + "|compte especes? titres")


class AccountsPage(LoggedPage, JsonPage):
    # actually, we parse the page as HTML, and lxml won't recognize utf-8-sig
    ENCODING = "utf-8"

    def build_doc(self, content):
        # Store the HTML doc to count the number of spaces
        self.html_doc = HTMLPage(self.browser, self.response).doc

        # Transform the HTML tag containing the accounts list into a JSON
        raw = re.search(r"syntheseController\.init\((.*)\)'>", content).group(1)
        d = json.JSONDecoder()
        # De-comment this line to debug the JSON accounts:
        # print(json.dumps(d.raw_decode(raw)[0]))
        return d.raw_decode(raw)[0]

    def count_spaces(self):
        """The total number of spaces corresponds to the number
        of available space choices plus the one we are on now.
        Some professional connections have a very specific xpath
        so we must look for nodes with 'idBamIndex' as well as
        "HubAccounts-link--cael" otherwise there might be space duplicates."""
        return (
            len(
                self.html_doc.xpath(
                    '//a[contains(@class, "HubAccounts-link--cael") and contains(@href, "idBamIndex=")]'
                )
            )
            + 1
        )

    def get_space_type(self):
        return Dict("marche")(self.doc)

    def get_owner_type(self):
        OWNER_TYPES = {
            "PARTICULIER": AccountOwnerType.PRIVATE,
            "HORS_MARCHE": AccountOwnerType.PRIVATE,
            "PROFESSIONNEL": AccountOwnerType.ORGANIZATION,
            "AGRICULTEUR": AccountOwnerType.ORGANIZATION,
            "PROMOTEURS": AccountOwnerType.ORGANIZATION,
            "ENTREPRISE": AccountOwnerType.ORGANIZATION,
            "PROFESSION_LIBERALE": AccountOwnerType.ORGANIZATION,
            "ASSOC_CA_MODERE": AccountOwnerType.ORGANIZATION,
        }
        return OWNER_TYPES.get(Dict("marche")(self.doc), NotAvailable)

    def get_connection_id(self):
        connection_id = Regexp(
            CleanText('//script[contains(text(), "NPC.utilisateur.ccptea")]'), r"NPC.utilisateur.ccptea = '(\d+)';"
        )(self.html_doc)
        return connection_id

    def has_main_account(self):
        return Dict("comptePrincipal", default=None)(self.doc)

    def has_profile_details(self):
        return CleanText('//a[text()="Gérer mes coordonnées"]')(self.html_doc)

    @method
    class get_main_account(ItemElement):
        klass = Account

        obj_id = CleanText(Dict("comptePrincipal/numeroCompte"))
        obj_number = CleanText(Dict("comptePrincipal/numeroCompte"))

        def obj_owner_type(self):
            return self.page.get_owner_type()

        def obj_label(self):
            if Field("owner_type")(self) == AccountOwnerType.PRIVATE:
                # All the accounts have the same owner if it is private,
                # so adding the owner in the libelle is useless.
                return CleanText(Dict("comptePrincipal/libelleProduit"))(self)
            return Format(
                "%s %s",
                CleanText(Dict("comptePrincipal/libelleProduit")),
                CleanText(Dict("comptePrincipal/libellePartenaireBam")),
            )(self)

        def obj_balance(self):
            balance = Dict("comptePrincipal/solde", default=NotAvailable)(self)
            if not empty(balance):
                return Eval(float_to_decimal, balance)(self)
            return NotAvailable

        obj_currency = CleanCurrency(Dict("comptePrincipal/idDevise"))
        obj_ownership = Map(
            CleanText(Dict("comptePrincipal/rolePartenaireCalcule")), ACCOUNT_OWNERSHIPS, default=NotAvailable
        )
        obj__index = Dict("comptePrincipal/index")
        obj__category = Dict("comptePrincipal/grandeFamilleProduitCode", default=None)
        obj__id_element_contrat = CleanText(Dict("comptePrincipal/idElementContrat"))
        obj__fam_product_code = CleanText(Dict("comptePrincipal/codeFamilleProduitBam"))
        obj__fam_contract_code = CleanText(Dict("comptePrincipal/codeFamilleContratBam"))

        def obj_type(self):
            _type = Map(CleanText(Dict("comptePrincipal/libelleUsuelProduit")), ACCOUNT_TYPES, Account.TYPE_UNKNOWN)(
                self
            )
            if _type == Account.TYPE_UNKNOWN:
                self.logger.warning(
                    'We got an untyped account: please add "%s" to ACCOUNT_TYPES.',
                    CleanText(Dict("comptePrincipal/libelleUsuelProduit"))(self),
                )
            return _type

    def has_main_cards(self):
        return Dict("comptePrincipal/cartesDD", default=None)(self.doc)

    @method
    class iter_main_cards(DictElement):
        item_xpath = "comptePrincipal/cartesDD"
        # Sometimes the server sends a list of cards containing a duplicate json's object
        # This will just send a warning instead raising an error
        ignore_duplicate = True

        class item(ItemElement):
            # Main account cards are all deferred and their
            # coming is already displayed with a '-' sign.

            klass = Account

            def condition(self):
                card_situation = Dict("codeSituationCarte")(self)
                if card_situation not in (5, 7):
                    # Cards with codeSituationCarte equal to 7 are active and present on the website
                    # Cards with codeSituationCarte equal to 5 are absent on the website, we skip them
                    self.logger.warning(
                        "codeSituationCarte unknown, Check if the %s card is present on the website", Field("id")(self)
                    )
                return card_situation != 5

            obj_id = CleanText(Dict("idCarte"), replace=[(" ", "")])

            obj_number = Field("id")
            obj_label = Format("Carte %s %s", Field("id"), CleanText(Dict("titulaire")))
            obj_type = Account.TYPE_CARD
            obj_coming = Eval(float_to_decimal, Dict("encoursCarteM"))
            obj_balance = Decimal(0)
            obj__index = Dict("index")
            obj__id_element_contrat = None

    @method
    class iter_accounts(DictElement):
        item_xpath = "grandesFamilles/*/elementsContrats"

        class item(ItemElement):
            IGNORED_ACCOUNT_FAMILIES = (
                "MES ASSURANCES",
                "VOS ASSURANCES",
            )

            klass = Account

            def obj_id(self):
                # Loan/credit ids may be duplicated so we use the contract number for now:
                if Field("type")(self) in (
                    Account.TYPE_LOAN,
                    Account.TYPE_CONSUMER_CREDIT,
                    Account.TYPE_REVOLVING_CREDIT,
                    Account.TYPE_MORTGAGE,
                ):
                    return CleanText(Dict("idElementContrat"))(self)
                return CleanText(Dict("numeroCompte"))(self)

            obj_ownership = Map(CleanText(Dict("rolePartenaireCalcule")), ACCOUNT_OWNERSHIPS, default=NotAvailable)
            obj_number = CleanText(Dict("numeroCompte"))
            obj_currency = CleanCurrency(Dict("idDevise"))
            obj__index = Dict("index")
            obj__category = Coalesce(
                Dict("grandeFamilleProduitCode", default=None),
                Dict("sousFamilleProduit/niveau", default=None),
                default=None,
            )
            obj__id_element_contrat = CleanText(Dict("idElementContrat"))
            obj__fam_product_code = CleanText(Dict("codeFamilleProduitBam"))
            obj__fam_contract_code = CleanText(Dict("codeFamilleContratBam"))

            def obj_owner_type(self):
                return self.page.get_owner_type()

            def obj_label(self):
                if Field("owner_type")(self) == AccountOwnerType.PRIVATE:
                    # All the accounts have the same owner if it is private,
                    # so adding the owner in the libelle is useless.
                    return CleanText(Dict("libelleProduit"))(self)
                return Format(
                    "%s %s",
                    CleanText(Dict("libelleProduit")),
                    CleanText(Dict("libellePartenaireBam")),
                )(self)

            def obj_type(self):
                if CleanText(Dict("libelleUsuelProduit"))(self) in ("HABITATION",):
                    # No need to log warning for "assurance" accounts
                    return NotAvailable
                _type = Map(CleanText(Dict("libelleUsuelProduit")), ACCOUNT_TYPES, Account.TYPE_UNKNOWN)(self)

                # MANDAT CTO Vendôme matches TYPE_LIFE_INSURANCE, although it's a TYPE_MARKET
                if _type == Account.TYPE_LIFE_INSURANCE and "MANDAT CTO" in CleanText(Dict("libelleProduit"))(self):
                    _type = Account.TYPE_MARKET

                if _type == Account.TYPE_UNKNOWN:
                    self.logger.warning(
                        'There is an untyped account: please add "%s" to ACCOUNT_TYPES.',
                        CleanText(Dict("libelleUsuelProduit"))(self),
                    )
                return _type

            def obj_balance(self):
                balance = Dict("solde", default=None)(self)
                if balance:
                    return Eval(float_to_decimal, balance)(self)
                # We will fetch the balance with account_details
                return NotAvailable

            def obj__is_liquidity(self):
                # Liquidities are fetched like an account as shown on the site
                # This attribute will be used to create_french_liquidity
                label = Lower(Field("label"), transliterate=True)(self)
                return ACCOUNT_IS_LIQUIDITY.match(label)

            def condition(self):
                # Ignore insurances (plus they all have identical IDs)
                # Ignore some credits not displayed on the website
                return (
                    Upper(Dict("familleProduit/libelle", default=""))(self) not in self.IGNORED_ACCOUNT_FAMILIES
                    and "non affiche" not in CleanText(Dict("sousFamilleProduit/libelle", default=""))(self)
                    and "Inactif" not in CleanText(Dict("libelleSituationContrat", default=""))(self)
                )


class AccountDetailsPage(LoggedPage, JsonPage):
    def get_account_balances(self):
        # We use the 'idElementContrat' key because it is unique
        # whereas the account id may not be unique for Loans
        balance_keys = (
            "solde",
            "encoursActuel",
            "valorisationContrat",
            "montantRestantDu",
            "capitalDisponible",
            "montantUtilise",
            "montantPlafondAutorise",
        )

        account_balances = {}
        for el in self.doc:
            # Insurances have no balance, we skip them
            if el.get("typeProduit") == "assurance":
                continue

            value = None
            for bal_key in balance_keys:
                if bal_key in el:
                    value = el[bal_key]
                    break

            if value is None:
                continue
            account_balances[Dict("idElementContrat")(el)] = float_to_decimal(value)

        return account_balances

    def get_loan_ids(self):
        # We use the 'idElementContrat' key because it is unique
        # whereas the account id may not be unique for Loans
        loan_ids = {}
        for el in self.doc:
            if el.get("numeroCredit"):
                # Loans
                loan_ids[Dict("idElementContrat")(el)] = Dict("numeroCredit")(el)
                loan_ids["numeroCompte"] = Dict("numeroCompte")(el)
            elif el.get("numeroContrat"):
                # Revolving credits
                loan_ids[Dict("idElementContrat")(el)] = Dict("numeroContrat")(el)
            elif el.get("numeroPret"):
                # Some pro Loans
                loan_ids[Dict("idElementContrat")(el)] = Dict("numeroPret")(el)
                loan_ids["numeroCompte"] = Dict("numeroCompte")(el)
        return loan_ids


class IbanPage(LoggedPage, JsonPage):
    def build_doc(self, content):
        # dict can have missing ending '"'
        # ex: '..."faxNumber":"},...'
        content = content.replace(':"}', ':""}')
        # ex: '..."phoneNumber":",...'
        content = re.sub(r'":","(?![,}])', '":"","', content)
        return super().build_doc(content)

    def get_iban(self):
        return Coalesce(
            Dict("ibanData/ibanCode", default=NotAvailable),
            Dict("ibanData/ibanData/ibanCode", default=NotAvailable),
        )(self.doc)


class HistoryPage(LoggedPage, JsonPage):
    def has_next_page(self):
        return Dict("hasNext")(self.doc)

    def get_next_index(self):
        return Dict("nextSetStartIndex")(self.doc)

    def has_history_transactions(self):
        return Dict("count")(self.doc)

    @method
    class iter_history(DictElement):
        item_xpath = "listeOperations"

        class item(ItemElement):

            TRANSACTION_TYPES = {
                "PAIEMENT PAR CARTE": FrenchTransaction.TYPE_CARD,
                "REMISE CARTE": FrenchTransaction.TYPE_CARD,
                "PRELEVEMENT CARTE": FrenchTransaction.TYPE_CARD_SUMMARY,
                "RETRAIT AU DISTRIBUTEUR": FrenchTransaction.TYPE_WITHDRAWAL,
                "RETRAIT MUR D'ARGENT": FrenchTransaction.TYPE_WITHDRAWAL,
                "FRAIS": FrenchTransaction.TYPE_BANK,
                "COTISATION": FrenchTransaction.TYPE_BANK,
                "VIREMENT": FrenchTransaction.TYPE_TRANSFER,
                "VIREMENT EN VOTRE FAVEUR": FrenchTransaction.TYPE_TRANSFER,
                "VIREMENT EMIS": FrenchTransaction.TYPE_TRANSFER,
                "CHEQUE EMIS": FrenchTransaction.TYPE_CHECK,
                "REMISE DE CHEQUE": FrenchTransaction.TYPE_DEPOSIT,
                "PRELEVEMENT": FrenchTransaction.TYPE_ORDER,
                "PRELEVT": FrenchTransaction.TYPE_ORDER,
                "PRELEVMNT": FrenchTransaction.TYPE_ORDER,
                "REMBOURSEMENT DE PRET": FrenchTransaction.TYPE_LOAN_PAYMENT,
                "REMISE D'EFFETS": FrenchTransaction.TYPE_PAYBACK,
                "AVOIR": FrenchTransaction.TYPE_PAYBACK,
                "VERSEMENT D'ESPECES": FrenchTransaction.TYPE_CASH_DEPOSIT,
                "INTERETS CREDITEURS": FrenchTransaction.TYPE_BANK,
            }

            klass = Transaction

            # There are 2 values in the json, dateOperation and dateValeur.
            # On the website they always display dateOperation.
            # dateValeur seems to be arbitrary (it is sometimes before the real
            # rdate of some transactions, which doesn't make any sense) so
            # we do not use it.
            obj_date = Date(CleanText(Dict("dateOperation")))

            obj_label = CleanText(
                Format(
                    "%s %s", CleanText(Dict("libelleTypeOperation", default="")), CleanText(Dict("libelleOperation"))
                )
            )

            # Transactions in foreign currencies have no 'libelleTypeOperation'
            # and 'libelleComplementaire' keys, hence the default values.
            # The CleanText() gets rid of additional spaces.
            def obj_raw(self):
                raw = CleanText(
                    Format(
                        "%s %s %s",
                        CleanText(Dict("libelleTypeOperation", default="")),
                        CleanText(Dict("libelleOperation")),
                        CleanText(Dict("libelleComplementaire", default="")),
                    )
                )

                try:
                    return Transaction.Raw(raw)(self)
                # Some date in transactions labels can cause troubles
                # So just setting rdate to NotAvailable
                except ParseError:
                    # Either a bad date, like November 31st
                    self.obj.rdate = NotAvailable
                    return raw(self)
                except ValueError as err:
                    if "month must be in" in str(err):
                        # Or not well formated
                        # Such has 0212121...
                        self.obj.rdate = NotAvailable
                        return raw(self)
                    raise

            def obj_type(self):
                return MapIn(Field("raw"), self.TRANSACTION_TYPES, Transaction.TYPE_UNKNOWN)(self)

            # If the patterns do not find the rdate in the label, we set the value
            # of rdate to date (dateOperation).
            def obj_rdate(self):
                date = Field("date")(self)
                # rdate is already set by `obj_raw` and the patterns.
                rdate = self.obj.rdate
                if empty(rdate) or rdate.year < 1970 or abs(rdate.year - date.year) >= 2 or rdate > date:
                    # website can send wrong date in label used to build rdate
                    # ex: "VIREMENT EN VOTRE FAVEUR TOTO 19.03.1214"
                    return NotAvailable
                elif rdate != date:
                    return rdate
                return date

            obj_amount = Eval(float_to_decimal, Dict("montant"))


class CardsPage(LoggedPage, JsonPage):
    @method
    class iter_card_parents(DictElement):
        item_xpath = "comptes"

        class iter_cards(DictElement):
            item_xpath = "listeCartes"
            # Sometimes the server sends a list of cards containing a duplicate json's object
            # This will just send a warning instead raising an error
            ignore_duplicate = True

            def parse(self, el):
                self.env["parent_id"] = Dict("idCompte")(el)

            class item(ItemElement):
                klass = Account

                obj_id = CleanText(Dict("idCarte"), replace=[(" ", "")])

                def condition(self):
                    assert CleanText(Dict("codeTypeDebitPaiementCarte"))(self) in ("D", "I")
                    return CleanText(Dict("codeTypeDebitPaiementCarte"))(self) == "D"

                obj_label = Format("Carte %s %s", Field("id"), CleanText(Dict("titulaire")))
                obj_type = Account.TYPE_CARD
                obj_coming = Eval(lambda x: -float_to_decimal(x), Dict("encoursCarteM"))
                obj_balance = CleanDecimal(0)
                obj__parent_id = Env("parent_id")
                obj__index = Dict("index")
                obj__id_element_contrat = None


class CardHistoryPage(LoggedPage, JsonPage):
    @method
    class iter_card_history(DictElement):
        item_xpath = None

        class item(ItemElement):
            klass = Transaction

            obj_label = CleanText(Dict("libelleOperation"))
            obj_raw = Transaction.Raw(CleanText(Dict("libelleOperation")))
            obj_amount = Eval(float_to_decimal, Dict("montant"))
            obj_type = Transaction.TYPE_DEFERRED_CARD
            obj_bdate = Field("rdate")

            def obj_date(self):
                return dateutil.parser.parse(Dict("datePrelevement")(self)).date()

            def obj_rdate(self):
                return dateutil.parser.parse(Dict("dateOperation")(self)).date()


class NetfincaRedirectionPage(LoggedPage, HTMLPage):
    def get_url(self):
        return Regexp(Attr("//body", "onload"), r'document.location="([^"]+)"')(self.doc)


class NetfincaHomePage(LoggedPage, HTMLPage):
    pass


class NetfincaLogoutToCragrPage(LoggedPage, HTMLPage):
    pass


class PredicaRedirectionPage(LoggedPage, HTMLPage):
    def on_load(self):
        form = self.get_form()
        form.submit()


class PredicaInvestmentsPage(LoggedPage, JsonPage):
    @method
    class iter_investments(DictElement):
        item_xpath = "listeSupports/support"

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(Dict("lcspt"))
            obj_valuation = Eval(float_to_decimal, Dict("mtvalspt"))

            def obj_portfolio_share(self):
                portfolio_share = Dict("txrpaspt", default=None)(self)
                if portfolio_share:
                    return Eval(lambda x: float_to_decimal(x / 100), portfolio_share)(self)
                return NotAvailable

            def obj_unitvalue(self):
                unit_value = Dict("mtliqpaaspt", default=None)(self)
                if unit_value:
                    return Eval(float_to_decimal, unit_value)(self)
                return NotAvailable

            def obj_quantity(self):
                quantity = Dict("qtpaaspt", default=None)(self)
                if quantity:
                    return Eval(float_to_decimal, quantity)(self)
                return NotAvailable

            def obj_diff(self):
                diff = Dict("mtpmvspt", default=None)(self)
                if diff is not None:
                    return Eval(float_to_decimal, diff)(self)
                return NotAvailable

            def obj_code(self):
                code = Dict("cdsptisn")(self)
                if is_isin_valid(code):
                    return code
                return NotAvailable

            def obj_code_type(self):
                if is_isin_valid(Field("code")(self)):
                    return Investment.CODE_TYPE_ISIN
                return NotAvailable


class LifeInsuranceInvestmentsPage(LoggedPage, HTMLPage):
    @method
    class iter_investments(ListElement):
        item_xpath = '//div[@id="menu1"]/div'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText('.//div[has-class("PrivateBank-tabsNavContentTitle")]')
            obj_valuation = CleanDecimal.French('.//div[contains(text(), "Valorisation")]/span')
            obj_quantity = CleanDecimal.French(
                './/div[contains(text(), "Nombre de parts")]/following-sibling::div[1]', default=NotAvailable
            )
            obj_unitvalue = CleanDecimal.French(
                './/div[contains(text(), "Valeur de la part")]/following-sibling::div[1]', default=NotAvailable
            )
            obj_diff = CleanDecimal.French('.//div[contains(text(), "+/- values")]/span', default=NotAvailable)

            def obj_portfolio_share(self):
                portfolio_share = CleanDecimal.French(
                    './/div[has-class("PrivateBank-tabsNavContentTitle")]/following-sibling::div/span',
                    default=NotAvailable,
                )(self)
                if not empty(portfolio_share):
                    return Eval(lambda x: x / 100, portfolio_share)(self)
                return NotAvailable


class BgpiRedirectionPage(LoggedPage, HTMLPage):
    def get_bgpi_url(self):
        # The HTML is broken so we cannot use a regular Attr('xpath')
        m = re.search(r'document.location="([^"]+)"', self.text)
        if m:
            return m.group(1)


class BgpiAccountsPage(LoggedPage, HTMLPage):
    def get_account_url(self, account_id):
        url = Link('//a[div[div[span[span[contains(text(), "%s")]]]]]' % account_id, default=None)(self.doc)
        if url:
            return urljoin("https://bgpi-gestionprivee.credit-agricole.fr", url)


class BgpiInvestmentsPage(LoggedPage, HTMLPage):
    @method
    class iter_investments(ListElement):
        item_xpath = "//div[div[ul[count(li) > 5]]]"

        class item(ItemElement):

            klass = Investment

            obj_label = CleanText('.//span[@class="uppercase"]')
            obj_code = IsinCode(CleanText('.//span[@class="cl-secondary"]'), default=NotAvailable)
            obj_code_type = IsinType(CleanText('.//span[@class="cl-secondary"]'), default=NotAvailable)
            obj_valuation = CleanDecimal.French(
                './/span[@class="box"][span[span[text()="Montant estimé"]]]/span[2]/span'
            )
            obj_quantity = CleanDecimal.French(
                './/span[@class="box"][span[span[text()="Nombre de part"]]]/span[2]/span'
            )
            obj_unitvalue = CleanDecimal.French(
                './/span[@class="box"][span[span[text()="Valeur liquidative"]]]/span[2]/span', default=NotAvailable
            )
            obj_unitprice = CleanDecimal.French(
                './/span[@class="box"][span[span[text()="Prix de revient"]]]/span[2]/span', default=NotAvailable
            )
            obj_portfolio_share = Eval(
                lambda x: x / 100,
                CleanDecimal.French('.//span[@class="box"][span[span[text()="Répartition"]]]/span[2]/span'),
            )

            def obj_diff_ratio(self):
                # Euro funds have '-' instead of a diff_ratio value
                text = CleanText('.//span[@class="box"][span[span[text()="+/- value latente (%)"]]]/span[2]/span')(self)
                if text in ("", "-"):
                    return NotAvailable
                return Eval(
                    lambda x: x / 100,
                    CleanDecimal.French(
                        './/span[@class="box"][span[span[text()="+/- value latente (%)"]]]/span[2]/span',
                    ),
                )(self)

            def obj_diff(self):
                if Field("diff_ratio")(self) == NotAvailable:
                    return NotAvailable
                return CleanDecimal.French(
                    './/span[@class="box"][span[span[text()="+/- value latente"]]]/span[2]/span'
                )(self)


class ProfilePage(LoggedPage, JsonPage):
    @method
    class get_user_profile(ItemElement):
        klass = Person

        obj_name = CleanText(Dict("displayName", default=NotAvailable))
        obj_birth_date = Date(Dict("birthdate", default=NotAvailable))

    @method
    class get_company_profile(ItemElement):
        klass = Company

        obj_name = CleanText(Dict("displayName", default=NotAvailable))
        obj_registration_date = Date(Dict("birthdate", default=NotAvailable))

    @method
    class get_advisor(ItemElement):
        klass = Advisor

        def obj_name(self):
            # If no advisor is displayed, we return the agency advisor.
            if Dict("advisorGivenName")(self) and Dict("advisorFamilyName")(self):
                return Format("%s %s", CleanText(Dict("advisorGivenName")), CleanText(Dict("advisorFamilyName")))(self)
            return Format(
                "%s %s", CleanText(Dict("branchManagerGivenName")), CleanText(Dict("branchManagerFamilyName"))
            )(self)


class ProfileDetailsPage(LoggedPage, HTMLPage):
    @method
    class fill_profile(ItemElement):
        obj_email = CleanText('//p[contains(@class, "Data mail")]', default=NotAvailable)
        obj_address = CleanText('//p[strong[contains(text(), "Adresse")]]/text()[2]', default=NotAvailable)

    @method
    class fill_advisor(ItemElement):
        obj_phone = CleanText(
            '//div[@id="blockConseiller"]//a[contains(@class, "advisorNumber")]', default=NotAvailable
        )


class ProProfileDetailsPage(ProfileDetailsPage):
    pass


class LoanRedirectionPage(LoggedPage, HTMLPage):
    def get_context_id(self):
        return Regexp(CleanText("//script"), r"context_id=([\w.-]+)")(self.doc)

    def get_ca_connect_url(self):
        # url has already built with all necessary params
        return Attr("//iframe", "src")(self.doc)


class LoanPage(LoggedPage, JsonPage):
    def get_auth_url(self):
        # Page contains just only one string with url
        return self.doc

    def get_client_id(self):
        return Dict("clientId")(self.doc)


class SofincoRedirectionPage(LoanRedirectionPage):
    pass


class SofincoUidPage(LoggedPage, JsonPage):
    def get_uid_session_contract(self):
        contract = self.response.json().get("contratRenouvelableActifDtoList")
        if not contract:
            return None
        return Dict("uid_session_contrat")(contract[0])


class SofincoTokenPage(LoggedPage, JsonPage):
    def get_bearer_token(self):
        return Dict("access_token")(self.doc)


class SofincoRevolvingCreditPage(LoggedPage, JsonPage):
    @method
    class fill_sofinco_revolving(ItemElement):
        obj_balance = CleanDecimal.SI(Dict("balanceAmount"), sign="-")
        obj_currency = "EUR"
        obj_total_amount = CleanDecimal.SI(Dict("attributedCapitalAmount", default=""), default=NotAvailable)
        obj_available_amount = CleanDecimal.SI(Dict("availableCapitalAmount", default=""), default=NotAvailable)
        obj_next_payment_amount = CleanDecimal.SI(Dict("monthlyPaymentAmount", default=""), default=NotAvailable)
        obj_next_payment_date = Date(Dict("nextDueDate", default=""), default=NotAvailable)
        obj_last_payment_date = Date(Dict("previousDueDate", default=""), default=NotAvailable)
        obj_maturity_date = Date(Dict("creditEndDate", default=""), default=NotAvailable)
        obj_nb_payments_left = Dict("numberOfRemainingDue", default=NotAvailable)
        obj_used_amount = CleanDecimal.SI(Dict("usedCapitalAmount", default=""), default=NotAvailable)

        def obj_rate(self):
            percentage_rate = CleanDecimal.SI(Dict("annualPercentageRateOfCharge", default=""), default=None)(self)
            if percentage_rate:
                return percentage_rate / 100
            return NotAvailable


class DetailsLoanPage(LoggedPage, JsonPage):
    @method
    class fill_loan(ItemElement):
        obj_total_amount = CleanDecimal.SI(Dict("resume/montant_emprunte/montant"))
        obj_rate = CleanDecimal.French(Dict("remboursement/taux_emprunt"))
        obj_next_payment_amount = CleanDecimal.SI(
            Dict("resume/montant_echeance/montant", default=NotAvailable), default=NotAvailable
        )
        obj_next_payment_date = Date(
            Dict("resume/date_prochaine_echeance", default=NotAvailable), dayfirst=True, default=NotAvailable
        )
        obj_maturity_date = Date(Dict("caracteristique_credit/date_fin"), dayfirst=True)
        obj_subscription_date = Date(Dict("caracteristique_credit/date_debut"), dayfirst=True)
        obj__insurance_rate = CleanDecimal.French(
            Dict("remboursement/taux_assurance", default=NotAvailable), default=NotAvailable
        )

        def obj_duration(self):
            duration = Dict("caracteristique_credit/duree_totale", default=NotAvailable)(self)
            if empty(duration):
                return NotAvailable
            return int(duration)

        def obj_deferred(self):
            # No case found yet with these fields filled
            if (
                Dict("remboursement/montant_differe", default=None)(self)
                or Dict("caracteristique_credit/periode_differe_debut", default=None)(self)
                or Dict("caracteristique_credit/periode_differe_fin", default=None)(self)
            ):
                # To throw after being triggered once
                # And check if the loan is necessarily deferred when at least one of these field is filled
                self.logger.warning("deferred fields are filled for: %s | id: %s", self.obj.label, self.obj.id)
            return NotAvailable

        # No case found yet, if an error is raised in the future, it will be easy to fix
        def obj_start_repayment_date(self):
            start_repayment_date = Date(
                Dict("caracteristique_credit/periode_debut_differe", default=None), default=NotAvailable
            )(self)
            # To throw after being triggered once
            # And verify if the key: periode_debut_differe provides the start_repayment_date
            if start_repayment_date:
                self.logger.warning(
                    "start_repayment_date may be available for: %s | id: %s", self.obj.label, self.obj.id
                )
            return NotAvailable


class RevolvingPage(LoggedPage, HTMLPage):
    @method
    class fill_revolving(ItemElement):
        obj_total_amount = CleanDecimal.French(
            '//span[text()="Capital attribué"]/ancestor::tr[1]/td[4]/span/text()', default=NotAvailable
        )
        obj_used_amount = CleanDecimal.French(
            '//span[text()="Capital utilisé"]/ancestor::tr[1]/td[4]/span/text()', default=NotAvailable
        )
        obj_available_amount = CleanDecimal.French(
            '//span[text()="Capital disponible"]/ancestor::tr[1]/td[4]/span/text()', default=NotAvailable
        )
        obj_next_payment_amount = CleanDecimal.French(
            '//span[text()="Montant du prochain prélèvement"]/ancestor::tr[1]/td[4]/span/text()', default=NotAvailable
        )
        obj_next_payment_date = Date(
            CleanText('//span[text()="Date du prochain prélèvement"]/ancestor::tr[1]/td[4]/span/text()'),
            dayfirst=True,
            default=NotAvailable,
        )
        obj_rate = CleanDecimal.French(
            '//span[text()="TAEG révisable :"]/ancestor::tr[1]/td[4]/span/text()', default=NotAvailable
        )

        def obj_balance(self):
            balance = Field("used_amount")(self)
            if empty(balance):
                return NotAvailable
            return -abs(balance)

    def back_to_home(self):
        self.get_form(name="formulaire").submit()

    def get_redirection_details(self):
        cookies = {}
        url = JSVar(Attr("//body", "onload"), "document.location")(self.doc)
        onload = JSVar(Attr("//body", "onload"), "document.cookie", nth="*")(self.doc)

        for cookie in onload:
            match = re.search(r"(cookie_\w+)=(\w+)", cookie)
            cookies.update({match.group(1): match.group(2)})

        return url, cookies


class RevolingErrorPage(LoggedPage, HTMLPage):
    pass


class ConsumerCreditPage(LoggedPage, HTMLPage):
    @method
    class fill_consumer_credit(ItemElement):
        obj_total_amount = CleanDecimal.French(
            Attr('//span[@id="span_mtpa74"]/input', "value", default=NotAvailable), default=NotAvailable
        )
        obj_used_amount = CleanDecimal.French(
            Attr('//span[@id="span_mkut14"]/input', "value", default=NotAvailable), default=NotAvailable
        )
        obj_available_amount = CleanDecimal.French(
            Attr('//span[@id="span_mtdis8"]/input', "value", default=NotAvailable), default=NotAvailable
        )
        obj_next_payment_amount = CleanDecimal.French(
            Attr('//span[@id="span_mprepb"]/input', "value", default=NotAvailable), default=NotAvailable
        )
        obj_next_payment_date = Date(
            Format(
                "%s/%s/%s",
                CleanText(Attr('//span[@id="span_dhppr"]/input[1]', "value", default="")),
                CleanText(Attr('//span[@id="span_dhppr"]/input[2]', "value", default="")),
                CleanText(Attr('//span[@id="span_dhppr"]/input[3]', "value", default="")),
            ),
            dayfirst=True,
            default=NotAvailable,
        )

        def obj_balance(self):
            balance = Field("used_amount")(self)
            if empty(balance):
                return NotAvailable
            return -abs(balance)

    def get_consumer_credit_redirection_url(self):
        return JSVar(Attr("//body", "onload"), "document.location")(self.doc)

    def get_consumer_credit_details_url(self):
        data = {}

        for elem in self.doc.xpath("//input"):
            data[elem.name] = elem.value
        data["gcFmkActionCode"] = re.search(r"try{(.+)SetTimeout", self.text).group(1)

        url = JSVar(CleanText("//script"), var="jsConstFmkPresentation")(self.doc)

        return url, data

    def back_to_home(self):
        self.get_form(name="formulaire").submit()


class EntRedirectionPage(LoanRedirectionPage):
    def get_first_ca_connect_url(self):
        return Regexp(CleanText("//script"), r"window\.location\.replace\(\"(\w.+)\"\)")(self.doc)

    def get_second_ca_connect_url(self):
        return Regexp(CleanText("//script"), r"window\.location\.href = \'(\w.+)\'")(self.doc)


class JsonRedirectionPage(LoggedPage, JsonPage):
    def get_from_json(self, json_key):
        return Dict(json_key)(self.doc)
