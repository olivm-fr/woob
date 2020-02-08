# -*- coding: utf-8 -*-

# Copyright(C) 2012 Romain Bignon
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

import re
import json
from datetime import datetime

from weboob.browser.pages import LoggedPage, HTMLPage, JsonPage
from weboob.browser.elements import DictElement, ItemElement, method
from weboob.browser.filters.standard import Date, CleanDecimal, CleanText, Format, Field, Env, Regexp, Currency
from weboob.browser.filters.json import Dict
from weboob.capabilities import NotAvailable
from weboob.capabilities.bank import Account, Loan
from weboob.capabilities.contact import Advisor
from weboob.capabilities.profile import Profile
from weboob.capabilities.bill import DocumentTypes, Subscription, Document
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.exceptions import BrowserUnavailable


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^CB (?P<text>.*?) FACT (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^RET(RAIT)? DAB (?P<dd>\d+)-(?P<mm>\d+)-.*', re.IGNORECASE), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^RET(RAIT)? DAB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<HH>\d{2})H(?P<MM>\d{2})', re.IGNORECASE), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^VIR(EMENT)?(\.PERIODIQUE)? (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^PRLV (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^CHEQUE.*', re.IGNORECASE), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(CONVENTION \d+ )?COTIS(ATION)? (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^\* (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^REMISE (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>.*)( \d+)? QUITTANCE .*', re.IGNORECASE), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^CB [\d\*]+ TOT DIF .*', re.IGNORECASE), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^CB [\d\*]+ (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^CB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'\*CB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^FAC CB (?P<text>.*?) (?P<dd>\d{2})/(?P<mm>\d{2})', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
    ]


class LoginPage(JsonPage):
    def get_response(self):
        return self.doc


class CenetLoginPage(HTMLPage):
    def login(self, username, password, nuser, codeCaisse, _id, vkpass):
        form = self.get_form(id='aspnetForm')

        form['__EVENTTARGET'] = "btn_authentifier_securise"
        form['__EVENTARGUMENT'] = '{"CodeCaisse":"%s","NumeroBad":"%s","NumeroUsager":"%s",\
                                    "MotDePasse":"%s","IdentifiantClavier":"%s","ChaineConnexion":"%s"}' \
                                    % (codeCaisse, username, nuser, password, _id, vkpass)

        form.submit()


class CenetHomePage(LoggedPage, HTMLPage):
    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = CleanText('//section[contains(@id, "ChargeAffaires")]//strong')
        obj_email = CleanText('//li[contains(@id, "MailContact")]')
        obj_phone = CleanText('//li[contains(@id, "TelAgence")]', replace=[('.', '')])
        obj_mobile = NotAvailable
        obj_agency = CleanText('//section[contains(@id, "Agence")]//strong')
        obj_address = CleanText('//li[contains(@id, "AdresseAgence")]')

        def obj_fax(self):
            return CleanText('//li[contains(@id, "FaxAgence")]', replace=[('.', '')])(self) or NotAvailable

    @method
    class get_profile(ItemElement):
        klass = Profile

        obj_name = CleanText('//li[@class="identite"]/a/span')


class CenetJsonPage(JsonPage):
    def __init__(self, browser, response, *args, **kwargs):
        super(CenetJsonPage, self).__init__(browser, response, *args, **kwargs)

        # Why you are so ugly....
        self.doc = json.loads(self.doc['d'])
        if self.doc['Erreur'] and (self.doc['Erreur']['Titre'] or self.doc['Erreur']['Code']):
            self.logger.warning('error on %r: %s', self.url, self.doc['Erreur']['Titre'] or self.doc['Erreur']['Code'])
            raise BrowserUnavailable(self.doc['Erreur']['Titre'] or self.doc['Erreur']['Description'])

        self.doc['DonneesSortie'] = json.loads(self.doc['DonneesSortie'])


class CenetAccountsPage(LoggedPage, CenetJsonPage):
    ACCOUNT_TYPES = {'CCP': Account.TYPE_CHECKING}

    @method
    class get_accounts(DictElement):
        item_xpath = "DonneesSortie"

        class item(ItemElement):
            klass = Account

            obj_id = CleanText(Dict('Numero'))
            obj_label = CleanText(Dict('Intitule'))
            obj_iban = CleanText(Dict('IBAN'))

            def obj_balance(self):
                absolut_amount = CleanDecimal(Dict('Solde/Valeur'))(self)
                if CleanText(Dict('Solde/CodeSens'))(self) == 'D':
                    return -absolut_amount
                return absolut_amount

            def obj_currency(self):
                return CleanText(Dict('Devise'))(self).upper()

            def obj_type(self):
                return self.page.ACCOUNT_TYPES.get(Dict('TypeCompte')(self), Account.TYPE_UNKNOWN)

            def obj__formated(self):
                return self.el


class CenetLoanPage(LoggedPage, CenetJsonPage):
    @method
    class get_accounts(DictElement):
        item_xpath = "DonneesSortie"

        class item(ItemElement):
            klass = Loan

            obj_id = CleanText(Dict('IdentifiantUniqueContrat'), replace=[(' ', '-')])
            obj_label = CleanText(Dict('Libelle'))
            obj_total_amount = CleanDecimal(Dict('MontantInitial/Valeur'))
            obj_currency = Currency(Dict('MontantInitial/Devise'))
            obj_type = Account.TYPE_LOAN
            obj_duration = CleanDecimal(Dict('Duree'))
            obj_rate = CleanDecimal.French(Dict('Taux'))
            obj_next_payment_amount = CleanDecimal(Dict('MontantProchaineEcheance/Valeur'))

            def obj_balance(self):
                balance = CleanDecimal(Dict('CapitalRestantDu/Valeur'))(self)
                if balance > 0:
                    balance *= -1
                return balance

            def obj_subscription_date(self):
                sub_date = Dict('DateDebutEffet')(self)
                if sub_date:
                    date = CleanDecimal().filter(sub_date) / 1000
                    return datetime.fromtimestamp(date).date()
                return NotAvailable

            def obj_maturity_date(self):
                mat_date = Dict('DateDerniereEcheance')(self)
                if mat_date:
                    date = CleanDecimal().filter(mat_date) / 1000
                    return datetime.fromtimestamp(date).date()
                return NotAvailable

            def obj_next_payment_date(self):
                next_date = Dict('DateProchaineEcheance')(self)
                if next_date:
                    date = CleanDecimal().filter(next_date) / 1000
                    return datetime.fromtimestamp(date).date()
                return NotAvailable


class CenetCardsPage(LoggedPage, CenetJsonPage):
    def get_cards(self):
        cards = Dict('DonneesSortie')(self.doc)

        # Remove dates to prevent bad parsing
        def reword_dates(card):
            tmp_card = card

            for k, v in tmp_card.items():
                if isinstance(v, dict):
                    v = reword_dates(v)
                if k == "Date" and v is not None and "Date" in v:
                    card[k] = None

        for card in cards:
            reword_dates(card)

        return cards


class CenetAccountHistoryPage(LoggedPage, CenetJsonPage):
    TR_TYPES_LABEL = {
        'VIR': Transaction.TYPE_TRANSFER,
        'CHEQUE': Transaction.TYPE_CHECK,
        'REMISE CHEQUE': Transaction.TYPE_CASH_DEPOSIT,
        'PRLV': Transaction.TYPE_ORDER,
    }

    TR_TYPES_API = {
        'VIR': Transaction.TYPE_TRANSFER,
        'PE': Transaction.TYPE_ORDER,  # PRLV
        'CE': Transaction.TYPE_CHECK,  # CHEQUE
        'DE': Transaction.TYPE_CASH_DEPOSIT,  # APPRO
        'PI': Transaction.TYPE_CASH_DEPOSIT,  # REMISE CHEQUE
    }

    @method
    class get_history(DictElement):
        item_xpath = "DonneesSortie"

        class item(ItemElement):
            klass = Transaction

            obj_raw = Format('%s %s', Dict('Libelle'), Dict('Libelle2'))
            obj_label = CleanText(Dict('Libelle'))
            obj_date = Date(Dict('DateGroupImputation'), dayfirst=True)
            obj_rdate = Date(Dict('DateGroupReglement'), dayfirst=True)

            def obj_type(self):
                ret = Transaction.TYPE_UNKNOWN

                # The API may send the same key for 'PRLV' and 'VIR' transactions
                # So the label is checked first, then the API key
                for k, v in self.page.TR_TYPES_LABEL.items():
                    if Field('label')(self).startswith(k):
                        ret = v
                        break

                if ret == Transaction.TYPE_UNKNOWN:
                    ret = self.page.TR_TYPES_API.get(Dict('TypeOperationDisplay')(self), Transaction.TYPE_UNKNOWN)

                if ret != Transaction.TYPE_UNKNOWN:
                    return ret

                for pattern, type in Transaction.PATTERNS:
                    if pattern.match(Field('raw')(self)):
                        return type

                return Transaction.TYPE_UNKNOWN

            def obj_amount(self):
                amount = CleanDecimal(Dict('Montant/Valeur'))(self)

                return -amount if Dict('Montant/CodeSens')(self) == "D" else amount

    def next_offset(self):
        offset = Dict('OffsetSortie')(self.doc)
        if offset:
            assert Dict('EstComplete')(self.doc) == 'false'
        return offset


class CenetCardSummaryPage(LoggedPage, CenetJsonPage):
    @method
    class get_history(DictElement):
        item_xpath = "DonneesSortie/OperationsCB"

        class item(ItemElement):
            klass = Transaction

            obj_label = CleanText(Dict('Libelle'))
            obj_date = Date(Dict('DateGroupImputation'), dayfirst=True)
            obj_type = Transaction.TYPE_DEFERRED_CARD

            def obj_raw(self):
                label = Dict('Libelle')(self)
                label2 = Dict('Libelle2')(self)
                if label2 and label2 != 'None':
                    return '%s %s' % (label, label2)
                else:
                    return label

            def obj_rdate(self):
                rdate = re.search('(FACT\s)(\d{6})', Field('label')(self))
                if rdate.group(2):
                    return Date(dayfirst=True).filter(rdate.group(2))
                return NotAvailable

            def obj_amount(self):
                amount = CleanDecimal(Dict('Montant/Valeur'))(self)

                return -amount if Dict('Montant/CodeSens')(self) == "D" else amount


class _LogoutPage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable(CleanText('//*[@class="messErreur"]')(self.doc))


class ErrorPage(_LogoutPage):
    pass


class UnavailablePage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable(CleanText('//div[@id="message_error_hs"]')(self.doc))


class SubscriptionPage(LoggedPage, CenetJsonPage):
    @method
    class iter_subscription(DictElement):
        item_xpath = 'DonneesSortie'

        class item(ItemElement):
            klass = Subscription

            obj_id = CleanText(Dict('Numero'))
            obj_label = CleanText(Dict('Intitule'))
            obj_subscriber = Env('subscriber')

    @method
    class iter_documents(DictElement):
        item_xpath = 'DonneesSortie'

        class item(ItemElement):
            klass = Document

            obj_id = Format('%s_%s_%s', Env('sub_id'), Dict('Numero'), CleanText(Env('french_date'), symbols='/'))
            obj_format = 'pdf'
            obj_type = DocumentTypes.OTHER
            obj__numero = CleanText(Dict('Numero'))
            obj__sub_id = Env('sub_id')
            obj__sub_label = Env('sub_label')
            obj__download_id = CleanText(Dict('IdDocument'))

            def obj_date(self):
                date = Regexp(Dict('DateArrete'), r'Date\((\d+)\)')(self)
                date = int(date) // 1000
                return datetime.fromtimestamp(date).date()

            def obj_label(self):
                return '%s %s' % (CleanText(Dict('Libelle'))(self), Env('french_date')(self))

            def parse(self, el):
                self.env['french_date'] = Field('date')(self).strftime('%d/%m/%Y')


class DownloadDocumentPage(LoggedPage, HTMLPage):
    def download_form(self, document):
        data = {
            'Numero': document._numero,
            'Libelle': document._sub_label.replace(' ', '+'),
            'DateArrete': '',
            'IdDocument': document._download_id
        }
        form = self.get_form(id='aspnetForm')
        form['__EVENTTARGET'] = 'btn_telecharger'
        form['__EVENTARGUMENT'] = json.dumps(data)
        return form.submit()
