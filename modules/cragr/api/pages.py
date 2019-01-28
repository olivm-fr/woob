# -*- coding: utf-8 -*-

# Copyright(C) 2012-2019 Romain Bignon
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

from __future__ import unicode_literals

from decimal import Decimal
import re
import json
import dateutil

from weboob.browser.pages import HTMLPage, JsonPage, LoggedPage
from weboob.capabilities import NotAvailable
from weboob.capabilities.bank import (
    Account, AccountOwnerType, Transaction,
)

from weboob.browser.elements import DictElement, ItemElement, method
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Currency as CleanCurrency, Format, Field, Map, Eval, Env,
)
from weboob.browser.filters.json import Dict


def float_to_decimal(f):
    return Decimal(str(f))


class KeypadPage(JsonPage):
    def build_password(self, password):
        # Fake Virtual Keyboard: just get the positions of each digit.
        key_positions = [i for i in Dict('keyLayout')(self.doc)]
        return str(','.join([str(key_positions.index(i)) for i in password]))

    def get_keypad_id(self):
        return Dict('keypadId')(self.doc)


class LoginPage(HTMLPage):
    def get_login_form(self, username, keypad_password, keypad_id):
        form = self.get_form(id="loginForm")
        form['j_username'] = username
        form['j_password'] = keypad_password
        form['keypadId'] = keypad_id
        return form


class LoggedOutPage(HTMLPage):
    def is_here(self):
        return self.doc.xpath('//b[text()="FIN DE CONNEXION"]')



class SecurityPage(JsonPage):
    def get_accounts_url(self):
        return Dict('url')(self.doc)


class ContractsPage(LoggedPage, HTMLPage):
    pass


ACCOUNT_TYPES = {
    'CCHQ': Account.TYPE_CHECKING, # par
    'CCOU': Account.TYPE_CHECKING, # pro
    'AUTO ENTRP': Account.TYPE_CHECKING, # pro
    'DEVISE USD': Account.TYPE_CHECKING,
    'EKO': Account.TYPE_CHECKING,
    'DAV NANTI': Account.TYPE_SAVINGS,
    'LIV A': Account.TYPE_SAVINGS,
    'LIV A ASS': Account.TYPE_SAVINGS,
    'LDD': Account.TYPE_SAVINGS,
    'PEL': Account.TYPE_SAVINGS,
    'CEL': Account.TYPE_SAVINGS,
    'CODEBIS': Account.TYPE_SAVINGS,
    'LJMO': Account.TYPE_SAVINGS,
    'CSL': Account.TYPE_SAVINGS,
    'LEP': Account.TYPE_SAVINGS,
    'LEF': Account.TYPE_SAVINGS,
    'TIWI': Account.TYPE_SAVINGS,
    'CSL LSO': Account.TYPE_SAVINGS,
    'CSL CSP': Account.TYPE_SAVINGS,
    'ESPE INTEG': Account.TYPE_SAVINGS,
    'DAV TIGERE': Account.TYPE_SAVINGS,
    'CPTEXCPRO': Account.TYPE_SAVINGS,
    'CPTEXCENT': Account.TYPE_SAVINGS,
    'DAT': Account.TYPE_SAVINGS,
    'PRET PERSO': Account.TYPE_LOAN,
    'P. ENTREPR': Account.TYPE_LOAN,
    'P. HABITAT': Account.TYPE_LOAN,
    'PRET 0%': Account.TYPE_LOAN,
    'INV PRO': Account.TYPE_LOAN,
    'TRES. PRO': Account.TYPE_LOAN,
    'CT ATT HAB': Account.TYPE_LOAN,
    'PEA': Account.TYPE_PEA,
    'PEAP': Account.TYPE_PEA,
    'DAV PEA': Account.TYPE_PEA,
    'CPS': Account.TYPE_MARKET,
    'TITR': Account.TYPE_MARKET,
    'TITR CTD': Account.TYPE_MARKET,
    'PVERT VITA': Account.TYPE_PERP,
    'réserves de crédit': Account.TYPE_CHECKING,
    'prêts personnels': Account.TYPE_LOAN,
    'crédits immobiliers': Account.TYPE_LOAN,
    'épargne disponible': Account.TYPE_SAVINGS,
    'épargne à terme': Account.TYPE_DEPOSIT,
    'épargne boursière': Account.TYPE_MARKET,
    'assurance vie et capitalisation': Account.TYPE_LIFE_INSURANCE,
    'PRED': Account.TYPE_LIFE_INSURANCE,
    'PREDI9 S2': Account.TYPE_LIFE_INSURANCE,
    'V.AVENIR': Account.TYPE_LIFE_INSURANCE,
    'FLORIA': Account.TYPE_LIFE_INSURANCE,
    'ATOUT LIB': Account.TYPE_REVOLVING_CREDIT,
}


class AccountsPage(LoggedPage, JsonPage):
    def build_doc(self, content):
        # Store the HTML doc to count the number of spaces
        self.html_doc = HTMLPage(self.browser, self.response).doc

        # Transform the HTML tag containing the accounts list into a JSON
        raw = re.search("syntheseController\.init\((.*)\)'>", content).group(1)
        d = json.JSONDecoder()
        # De-comment this line to debug the JSON accounts:
        # print json.dumps(d.raw_decode(raw)[0])
        return d.raw_decode(raw)[0]

    def count_spaces(self):
        # The total number of spaces corresponds to the number
        # of available space choices plus the one we are on now:
        return len(self.html_doc.xpath('//div[@class="HubAccounts-content"]/a')) + 1

    def get_owner_type(self):
        OWNER_TYPES = {
            'PARTICULIER':   AccountOwnerType.PRIVATE,
            'PROFESSIONNEL': AccountOwnerType.ORGANIZATION,
            'ASSOC_CA_MODERE': AccountOwnerType.ORGANIZATION,
        }
        return OWNER_TYPES.get(Dict('marche')(self.doc), NotAvailable)

    @method
    class get_main_account(ItemElement):
        klass = Account

        obj_id = CleanText(Dict('comptePrincipal/numeroCompte'))
        obj_number = CleanText(Dict('comptePrincipal/numeroCompte'))
        obj_label = CleanText(Dict('comptePrincipal/libelleProduit'))
        obj_balance = Eval(float_to_decimal, Dict('comptePrincipal/solde'))
        obj_currency = CleanCurrency(Dict('comptePrincipal/idDevise'))
        obj__index = Dict('comptePrincipal/index')
        obj__category = Dict('comptePrincipal/grandeFamilleProduitCode', default=None)
        obj__id_element_contrat = CleanText(Dict('comptePrincipal/idElementContrat'))

        def obj_type(self):
            _type = Map(CleanText(Dict('comptePrincipal/libelleUsuelProduit')), ACCOUNT_TYPES, Account.TYPE_UNKNOWN)(self)
            if _type == Account.TYPE_UNKNOWN:
                self.logger.warning('We got an untyped account: please add "%s" to ACCOUNT_TYPES.' % CleanText(Dict('comptePrincipal/libelleUsuelProduit'))(self))
            return _type

    @method
    class iter_accounts(DictElement):
        item_xpath = 'grandesFamilles/*/elementsContrats'

        class item(ItemElement):
            IGNORED_ACCOUNTS = ("MES ASSURANCES",)

            klass = Account

            def obj_id(self):
                # Loan ids may be duplicated so we use the contract number for now:
                if Field('type')(self) == Account.TYPE_LOAN:
                    return CleanText(Dict('idElementContrat'))(self)
                return CleanText(Dict('numeroCompte'))(self)

            obj_number = CleanText(Dict('numeroCompte'))
            obj_label = CleanText(Dict('libelleProduit'))
            obj_currency = CleanCurrency(Dict('idDevise'))
            obj__index = Dict('index')
            obj__category = Dict('grandeFamilleProduitCode', default=None)
            obj__id_element_contrat = CleanText(Dict('idElementContrat'))

            def obj_type(self):
                _type = Map(CleanText(Dict('libelleUsuelProduit')), ACCOUNT_TYPES, Account.TYPE_UNKNOWN)(self)
                if _type == Account.TYPE_UNKNOWN:
                    self.logger.warning('There is an untyped account: please add "%s" to ACCOUNT_TYPES.' % CleanText(Dict('libelleUsuelProduit'))(self))
                return _type

            def obj_balance(self):
                balance = Dict('solde', default=None)(self)
                if balance:
                    return Eval(float_to_decimal, balance)(self)
                # We will fetch the balance with account_details
                return NotAvailable

            def condition(self):
                # Ignore insurances (plus they all have identical IDs)
                return CleanText(Dict('familleProduit/libelle', default=''))(self) not in self.IGNORED_ACCOUNTS


class AccountDetailsPage(LoggedPage, JsonPage):
    def get_account_balances(self):
        # We use the 'idElementContrat' key because it is unique
        # whereas the account id may not be unique for Loans
        account_balances = {}
        for el in self.doc:
            value = el.get('solde', el.get('encoursActuel', el.get('valorisationContrat', el.get('montantRestantDu', el.get('capitalDisponible')))))
            assert value is not None, 'Could not find the account balance'
            account_balances[Dict('idElementContrat')(el)] = float_to_decimal(value)
        return account_balances

    def get_loan_ids(self):
        # We use the 'idElementContrat' key because it is unique
        # whereas the account id may not be unique for Loans
        loan_ids = {}
        for el in self.doc:
            if el.get('numeroCredit'):
                # Loans
                loan_ids[Dict('idElementContrat')(el)] = Dict('numeroCredit')(el)
            elif el.get('numeroContrat'):
                # Revolving credits
                loan_ids[Dict('idElementContrat')(el)] = Dict('numeroContrat')(el)
        return loan_ids


class IbanPage(LoggedPage, JsonPage):
    def get_iban(self):
        return Dict('ibanData/ibanCode', default=NotAvailable)(self.doc)


class HistoryPage(LoggedPage, JsonPage):
    def has_next_page(self):
        return Dict('hasNext')(self.doc)

    def get_next_index(self):
        return Dict('nextSetStartIndex')(self.doc)

    @method
    class iter_history(DictElement):
        item_xpath = 'listeOperations'

        class item(ItemElement):

            TRANSACTION_TYPES = {
                'PAIEMENT PAR CARTE':        Transaction.TYPE_CARD,
                'REMISE CARTE':              Transaction.TYPE_CARD,
                'PRELEVEMENT CARTE':         Transaction.TYPE_CARD_SUMMARY,
                'RETRAIT AU DISTRIBUTEUR':   Transaction.TYPE_WITHDRAWAL,
                "RETRAIT MUR D'ARGENT":      Transaction.TYPE_WITHDRAWAL,
                'FRAIS':                     Transaction.TYPE_BANK,
                'COTISATION':                Transaction.TYPE_BANK,
                'VIREMENT':                  Transaction.TYPE_TRANSFER,
                'VIREMENT EN VOTRE FAVEUR':  Transaction.TYPE_TRANSFER,
                'VIREMENT EMIS':             Transaction.TYPE_TRANSFER,
                'CHEQUE EMIS':               Transaction.TYPE_CHECK,
                'REMISE DE CHEQUE':          Transaction.TYPE_DEPOSIT,
                'PRELEVEMENT':               Transaction.TYPE_ORDER,
                'PRELEVT':                   Transaction.TYPE_ORDER,
                'PRELEVMNT':                 Transaction.TYPE_ORDER,
                'REMBOURSEMENT DE PRET':     Transaction.TYPE_LOAN_PAYMENT,
            }

            klass = Transaction

            obj_raw = Format('%s %s %s', CleanText(Dict('libelleTypeOperation')), CleanText(Dict('libelleOperation')), CleanText(Dict('libelleComplementaire')))
            obj_label = Format('%s %s', CleanText(Dict('libelleTypeOperation')), CleanText(Dict('libelleOperation')))
            obj_amount = Eval(float_to_decimal, Dict('montant'))
            obj_type = Map(CleanText(Dict('libelleTypeOperation')), TRANSACTION_TYPES, Transaction.TYPE_UNKNOWN)

            def obj_date(self):
                return dateutil.parser.parse(Dict('dateValeur')(self))

            def obj_rdate(self):
                return dateutil.parser.parse(Dict('dateOperation')(self))


class CardsPage(LoggedPage, JsonPage):
    @method
    class iter_card_parents(DictElement):
        item_xpath = 'comptes'

        class iter_cards(DictElement):
            item_xpath = 'listeCartes'

            def parse(self, el):
                self.env['parent_id'] = Dict('idCompte')(el)

            class item(ItemElement):
                klass = Account

                def obj_id(self):
                    return CleanText(Dict('idCarte'))(self).replace(' ', '')

                def condition(self):
                    assert CleanText(Dict('codeTypeDebitPaiementCarte'))(self) in ('D', 'I')
                    return CleanText(Dict('codeTypeDebitPaiementCarte'))(self)=='D'

                obj_label = Format('Carte %s %s', Field('id'), CleanText(Dict('titulaire')))
                obj_type = Account.TYPE_CARD
                obj_coming = Eval(lambda x: -float_to_decimal(x), Dict('encoursCarteM'))
                obj_balance = CleanDecimal(0)
                obj__parent_id = Env('parent_id')
                obj__index = Dict('index')
                obj__id_element_contrat = None


class CardHistoryPage(LoggedPage, JsonPage):
    @method
    class iter_card_history(DictElement):
        item_xpath = None

        class item(ItemElement):
            klass = Transaction

            obj_raw = CleanText(Dict('libelleOperation'))
            obj_label = CleanText(Dict('libelleOperation'))
            obj_amount = Eval(float_to_decimal, Dict('montant'))
            obj_type = Transaction.TYPE_DEFERRED_CARD

            def obj_date(self):
                return dateutil.parser.parse(Dict('datePrelevement')(self))

            def obj_rdate(self):
                return dateutil.parser.parse(Dict('dateOperation')(self))


class InvestmentPage(LoggedPage, JsonPage):
    pass


class ProfilePage(LoggedPage, JsonPage):
    pass