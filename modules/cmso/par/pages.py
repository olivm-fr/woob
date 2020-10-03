# -*- coding: utf-8 -*-

# Copyright(C) 2016      Edouard Lambert
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

import re
import requests
import json
import datetime as dt
from hashlib import md5

from collections import OrderedDict

from weboob.exceptions import BrowserUnavailable
from weboob.browser.pages import HTMLPage, JsonPage, RawPage, LoggedPage, pagination
from weboob.browser.elements import DictElement, ItemElement, TableElement, SkipItem, method
from weboob.browser.filters.standard import (
    CleanText, Upper, Date, Regexp, Format, CleanDecimal, Filter, Env, Slugify,
    Field, Currency, Map, Base, MapIn,
)
from weboob.browser.filters.json import Dict
from weboob.browser.filters.html import Attr, Link, TableCell, AbsoluteLink
from weboob.browser.exceptions import ServerError
from weboob.capabilities.bank import Account, Loan, AccountOwnership
from weboob.capabilities.wealth import Investment, MarketOrder, MarketOrderDirection, MarketOrderType
from weboob.capabilities.contact import Advisor
from weboob.capabilities.base import NotAvailable
from weboob.capabilities.profile import Profile
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.exceptions import ParseError
from weboob.tools.capabilities.bank.investments import IsinCode, IsinType
from weboob.tools.compat import unicode


class LoginPage(HTMLPage):
    pass


class LogoutPage(RawPage):
    pass


class SpacesPage(LoggedPage, JsonPage):
    def get_part_space(self):
        for abo in Dict('listAbonnement')(self.doc):
            if Dict('espaceProxy')(abo) == 'PART':
                return Dict('numContratBAD')(abo)


class ChangeSpacePage(LoggedPage, JsonPage):
    def get_access_token(self):
        return Dict('accessToken')(self.doc)


class AccountsPage(LoggedPage, JsonPage):
    TYPES = OrderedDict([
        ('courant', Account.TYPE_CHECKING),
        ('pee', Account.TYPE_PEE),
        ('epargne en actions', Account.TYPE_PEA),
        ('pea', Account.TYPE_PEA),
        ('p.e.a.', Account.TYPE_PEA),
        ('preference', Account.TYPE_LOAN),
        ('livret', Account.TYPE_SAVINGS),
        ('vie', Account.TYPE_LIFE_INSURANCE),
        ('previ_option', Account.TYPE_LIFE_INSURANCE),
        ('avantage capitalisation', Account.TYPE_LIFE_INSURANCE),
        ('actions', Account.TYPE_MARKET),
        ('titres', Account.TYPE_MARKET),
        ('ldd cm', Account.TYPE_SAVINGS),
        ('librissime', Account.TYPE_SAVINGS),
        ('epargne logement', Account.TYPE_SAVINGS),
        ('plan bleu', Account.TYPE_SAVINGS),
        ('capital plus', Account.TYPE_SAVINGS),
        ('capital expansion', Account.TYPE_DEPOSIT),
        ('carte', Account.TYPE_CARD),
        ('previ-retraite', Account.TYPE_PERP),
    ])

    def get_keys(self):
        """Returns the keys for which the value is a list or dict"""
        keys = [k for k, v in self.doc.items() if v and isinstance(v, (dict, list)) and k != 'exception']
        # A 400 error can sometimes be present in the json even if there are accounts
        if "exception" in self.doc and self.doc['exception'].get('code') != 400 and not keys:
            return []
        return keys

    def check_response(self):
        if "exception" in self.doc:
            self.logger.warning(
                "There are no checking accounts: exception %r with code %s",
                self.doc['exception']['message'],
                self.doc['exception']['code']
            )

    def get_numbers(self):
        keys = self.get_keys()
        numbers = {}
        for key in keys:
            if isinstance(self.doc[key], dict):
                keys_ = [k for k in self.doc[key] if isinstance(k, unicode)]
                contracts = [v for k in keys_ for v in self.doc[key][k]]
            else:
                contracts = [v for v in self.doc[key]]
            numbers.update({c['index']: c['numeroContratSouscrit'] for c in contracts})
        return numbers

    @method
    class iter_accounts(DictElement):
        def parse(self, el):
            self.item_xpath = "%s/*" % Env('key')(self)

        def find_elements(self):
            selector = self.item_xpath.split('/')
            for sub_element in selector:
                if isinstance(self.el, dict) and self.el and sub_element == '*':
                    self.el = next(iter(self.el.values()))  # replace self.el with its first value
                if sub_element == '*':
                    continue
                self.el = self.el[sub_element]
            for sub_element in self.el:
                yield sub_element

        class item(ItemElement):
            klass = Account

            def condition(self):
                return "LIVRET" not in Dict('accountType')(self.el)

            obj_id = Dict('numeroContratSouscrit')
            obj_label = Upper(Dict('lib'))
            obj_currency = Dict('deviseCompteCode')
            obj_coming = CleanDecimal(Dict('AVenir', default=None), default=NotAvailable)
            # Iban is available without last 5 numbers, or by sms
            obj_iban = NotAvailable
            obj__index = Dict('index')
            # Need this to match with internal recipients
            # and to do transfer
            obj__bic = Dict('bic', default=NotAvailable)

            def obj__owner_name(self):
                co_owner_name = CleanText(Dict('nomCotitulaire', default=''))(self)
                if co_owner_name:
                    co_owner_firstname = CleanText(Dict('prenomCotitulaire', default=''))(self)
                    # The `nomCotitulaire` sometimes contains both last name and
                    # first name, sometimes just the last name.
                    if co_owner_firstname:
                        co_owner_name = '%s %s' % (co_owner_name, co_owner_firstname)

                    return '%s %s / %s' % (
                        Upper(Dict('nomClient'))(self),
                        Upper(Dict('prenomClient'))(self),
                        co_owner_name.upper(),
                    )
                return Format(
                    '%s %s',
                    Upper(Dict('nomClient')),
                    Upper(Dict('prenomClient')),
                )(self)

            def obj__recipient_id(self):
                # The owner name is swapped (firstname lastname -> lastname firstname)
                # between the request in iter_accounts and the requests
                # listing recipients. Sorting the owner name is a way to
                # have the same md5 hash in both of those cases.
                to_hash = '%s %s' % (
                    Upper(Field('label'))(self),
                    ''.join(sorted(Field('_owner_name')(self))),
                )
                return md5(to_hash.encode('utf-8')).hexdigest()

            def obj_balance(self):
                balance = CleanDecimal(Dict('soldeEuro', default="0"))(self)
                if Field('type')(self) == Account.TYPE_LOAN:
                    balance = -abs(balance)
                return balance

            # It can have revolving credit on this page
            def obj__total_amount(self):
                return CleanDecimal(Dict('grantedAmount', default=None), default=NotAvailable)(self)

            def obj_type(self):
                return self.page.TYPES.get(Dict('accountType', default=None)(self).lower(), Account.TYPE_UNKNOWN)

            def obj_ownership(self):
                if Dict('accountListType')(self) == 'COMPTE_MANDATAIRE':
                    return AccountOwnership.ATTORNEY
                elif Dict('nomCotitulaire', default=None)(self):
                    return AccountOwnership.CO_OWNER
                return AccountOwnership.OWNER

    @method
    class iter_savings(DictElement):
        @property
        def item_xpath(self):
            return "%s/*/savingsProducts" % Env('key')(self)

        def store(self, obj):
            id = obj.id
            n = 1
            while id in self.objects:
                self.logger.warning('There are two objects with the same ID! %s' % id)
                n += 1
                id = '%s-%s' % (obj.id, n)

            obj.id = id
            self.objects[obj.id] = obj
            return obj

        # the accounts really are deeper, but the account type is in a middle-level
        class iter_accounts(DictElement):
            item_xpath = 'savingsAccounts'

            def parse(self, el):
                # accounts may have a user-entered label, so it shouldn't be relied too much on for parsing the account type
                self.env['type_label'] = el['libelleProduit']

            def store(self, obj):
                id = obj.id
                n = 1
                while id in self.objects:
                    self.logger.warning('There are two objects with the same ID! %s' % id)
                    n += 1
                    id = '%s-%s' % (obj.id, n)

                obj.id = id
                self.objects[obj.id] = obj
                return obj

            class item(ItemElement):
                klass = Account

                obj_label = Upper(Dict('libelleContrat'))
                obj_balance = CleanDecimal(Dict('solde', default="0"))
                obj_currency = 'EUR'
                obj_coming = CleanDecimal(Dict('AVenir', default=None), default=NotAvailable)
                obj__index = Dict('index')
                obj__owner = Dict('nomTitulaire')

                def obj_id(self):
                    type = Field('type')(self)
                    if type == Account.TYPE_LIFE_INSURANCE:
                        number = self.get_lifenumber()
                        if number:
                            return number
                    elif type in (Account.TYPE_PEA, Account.TYPE_MARKET):
                        number = self.get_market_number()
                        if number:
                            return number

                    try:
                        return Env('numbers')(self)[Dict('index')(self)]
                    except KeyError:
                        # index often changes, so we can't use it... and have to do something ugly
                        return Slugify(Format('%s-%s', Dict('libelleContrat'), Dict('nomTitulaire')))(self)

                def obj_type(self):
                    for key in self.page.TYPES:
                        if key in Env('type_label')(self).lower():
                            return self.page.TYPES[key]
                    return Account.TYPE_UNKNOWN

                def obj_ownership(self):
                    if Dict('nomCotitulaire', default=None)(self):
                        return AccountOwnership.CO_OWNER

                    owner = Dict('nomTitulaire', default=None)(self)

                    if owner and all(n in owner.upper() for n in self.env['name'].split()):
                        return AccountOwnership.OWNER
                    return AccountOwnership.ATTORNEY

                def get_market_number(self):
                    label = Field('label')(self)
                    try:
                        page = self.page.browser._go_market_history('historiquePortefeuille')
                        return page.get_account_id(label, Field('_owner')(self))
                    finally:
                        self.page.browser._return_from_market()

                def get_lifenumber(self):
                    index = Dict('index')(self)
                    data = json.loads(self.page.browser.redirect_insurance.open(accid=index).text)
                    if not data:
                        raise SkipItem('account seems unavailable')
                    url = data['url']
                    page = self.page.browser.open(url).page
                    return page.get_account_id()

                # Need those to match with internal recipients
                # and to do transfer
                obj__bic = Dict('bic', default=NotAvailable)

                def obj__owner_name(self):
                    co_owner_name = CleanText(Dict('nomCotitulaire', default=''))(self)
                    if co_owner_name:
                        co_owner_firstname = CleanText(Dict('prenomCotitulaire', default=''))(self)
                        # The `nomCotitulaire` sometimes contains both last name
                        # and first name, sometimes just the last name.
                        if co_owner_firstname:
                            co_owner_name = '%s %s' % (co_owner_name, co_owner_firstname)

                        return '%s / %s' % (
                            Upper(Field('_owner'))(self),
                            co_owner_name.upper(),
                        )
                    return Upper(Field('_owner'))(self)

                def obj__recipient_id(self):
                    # The owner name is swapped (firstname lastname -> lastname firstname)
                    # between the request in iter_accounts and the requests
                    # listing recipients. Sorting the owner name is a way to
                    # have the same md5 hash in both of those cases.
                    to_hash = '%s %s' % (
                        Upper(Field('label'))(self),
                        ''.join(sorted(Field('_owner_name')(self))),
                    )
                    return md5(to_hash.encode('utf-8')).hexdigest()

    @method
    class iter_loans(DictElement):
        def parse(self, el):
            self.item_xpath = Env('key')(self)
            if "Pret" in Env('key')(self):
                self.item_xpath = "%s/*/lstPret" % self.item_xpath

        class item(ItemElement):
            klass = Loan

            def obj_id(self):
                # it seems that if we don't have "numeroContratSouscrit", "identifiantTechnique" is unique : only this direction !
                return Dict('numeroContratSouscrit', default=None)(self) or Dict('identifiantTechnique')(self)

            obj_label = Dict('libelle')
            obj_currency = 'EUR'
            obj_type = Account.TYPE_LOAN

            def obj_total_amount(self):
                # Json key change depending on loan type, consumer credit or revolving credit
                return CleanDecimal(Dict('montantEmprunte', default=None)(self) or Dict('montantUtilise'))(self)

            # Key not always available, when revolving credit not yet consummed
            obj_next_payment_amount = CleanDecimal(Dict('montantProchaineEcheance', default=None), default=NotAvailable)

            # obj_rate = can't find the info on website except pdf :(

            # Dates scraped are timestamp, to remove last '000' we divide by 1000
            def obj_maturity_date(self):
                # Key not always available, when revolving credit not yet consummed
                timestamp = Dict('dateFin', default=None)(self)
                if timestamp:
                    return dt.date.fromtimestamp(timestamp / 1000)
                return NotAvailable

            def obj_next_payment_date(self):
                # Key not always available, when revolving credit not yet consummed
                timestamp = Dict('dateProchaineEcheance', default=None)(self)
                if timestamp:
                    return dt.date.fromtimestamp(timestamp / 1000)
                return NotAvailable

            def obj_balance(self):
                return -abs(CleanDecimal().filter(self.el.get('montantRestant', self.el.get('montantUtilise'))))

            # only for revolving loans
            obj_available_amount = CleanDecimal(Dict('montantDisponible', default=None), default=NotAvailable)

            def obj_ownership(self):
                if Dict('nomCotitulaire', default=None)(self):
                    return AccountOwnership.CO_OWNER
                return AccountOwnership.OWNER


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^CARTE (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^(?P<text>(PRLV|PRELEVEMENTS).*)'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^(?P<text>RET DAB.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^(?P<text>ECH.*)'), FrenchTransaction.TYPE_LOAN_PAYMENT),
        (re.compile(r'^(?P<text>VIR.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^(?P<text>ANN.*)'), FrenchTransaction.TYPE_PAYBACK),
        (re.compile(r'^(?P<text>(VRST|VERSEMENT).*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>CHQ.*)'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(?P<text>.*)'), FrenchTransaction.TYPE_BANK),
    ]


class HistoryPage(LoggedPage, JsonPage):
    def has_deferred_cards(self):
        return Dict('pendingDeferredDebitCardList/currentMonthCardList', default=None)(self.doc)

    def get_keys(self):
        if 'exception' in self.doc:
            return []
        return [k for k, v in self.doc.items() if v and isinstance(v, (dict, list))]

    @pagination
    @method
    class iter_history(DictElement):
        def next_page(self):
            if len(Env('nbs', default=[])(self)):
                data = {'index': Env('index')(self)}
                if Env('nbs')(self)[0] != "SIX_DERNIERES_SEMAINES":
                    data.update({'filtreOperationsComptabilisees': "MOIS_MOINS_%s" % Env('nbs')(self)[0]})
                Env('nbs')(self).pop(0)
                return requests.Request('POST', data=json.dumps(data))

        def parse(self, el):
            exception = Dict('exception', default=None)(self)
            if exception:
                message = exception.get('message', '')
                assert 'SERVICE_INDISPONIBLE' in message, 'Unknown error in history page: "%s"' % message
                # The error message is a stack trace so we do not
                # send it.
                raise BrowserUnavailable()
            # Key only if coming
            key = Env('key', default=None)(self)
            if key:
                if "CardList" in key:
                    self.item_xpath = "%s/currentMonthCardList/*/listeOperations" % key
                elif "futureOperationList" in key:
                    self.item_xpath = "%s/futurePrelevementList" % key
                else:
                    self.item_xpath = "%s/operationList" % key
            else:
                self.item_xpath = "listOperationProxy"

        class item(ItemElement):
            klass = Transaction

            class FromTimestamp(Filter):
                def filter(self, timestamp):
                    try:
                        return dt.date.fromtimestamp(int(timestamp[:-3]))
                    except TypeError:
                        return self.default_or_raise(ParseError('Element %r not found' % self.selector))

            obj_date = FromTimestamp(Dict('dateOperation', default=NotAvailable), default=NotAvailable)
            obj_raw = Transaction.Raw(Dict('libelleCourt'))
            obj_vdate = Date(Dict('dateValeur', NotAvailable), dayfirst=True, default=NotAvailable)
            obj_amount = CleanDecimal(Dict('montantEnEuro'), default=NotAvailable)
            obj_id = Dict('clefDomirama', default='')
            # 'operationId' is different between each session, we use it to avoid duplicates on a unique
            # session
            obj__operationid = Dict('operationId', default='')

            def parse(self, el):
                key = Env('key', default=None)(self)
                if key and "DeferredDebit" in key:
                    for x in Dict('%s/currentMonthCardList' % key)(self.page.doc):
                        deferred_date = Dict('dateDiffere', default=None)(x)
                        if deferred_date:
                            break
                    self.obj._deferred_date = self.FromTimestamp().filter(deferred_date)


class RedirectInsurancePage(LoggedPage, JsonPage):
    def get_url(self):
        return Dict('url')(self.doc)


class LifeinsurancePage(LoggedPage, HTMLPage):
    def get_account_id(self):
        account_id = Regexp(CleanText('//h1[@class="portlet-title"]'), r'n° ([\s\w]+)', default=NotAvailable)(self.doc)
        if account_id:
            return re.sub(r'\s', '', account_id)

    def get_link(self, page):
        return Link(default=NotAvailable).filter(self.doc.xpath('//a[contains(text(), "%s")]' % page))

    @method
    class iter_accounts(TableElement):
        item_xpath = '//div[@class="tabAssuranceVieCapi"]//table/tbody/tr[has-class("results-row")]'
        head_xpath = '//div[@class="tabAssuranceVieCapi"]//table/thead/tr/th'

        col_label = 'Contrat'
        col_id = 'Numéro'
        col_balance = 'Solde'

        class item(ItemElement):
            klass = Account

            obj_id = CleanText(TableCell('id'), replace=[(' ', '')])
            obj_label = CleanText(TableCell('label'))
            obj_balance = CleanDecimal.French(TableCell('balance'))
            obj_currency = Currency(TableCell('balance'))
            obj_type = Account.TYPE_LIFE_INSURANCE

            def obj_url(self):
                return AbsoluteLink(TableCell('id')(self)[0].xpath('.//a'), default=NotAvailable)(self)

    @method
    class fill_account(ItemElement):
        def obj_valuation_diff_ratio(self):
            valuation_diff_percent = CleanDecimal.French(
                '//div[@class="perfContrat"]/span[@class="value"]',
                default=None
            )(self)

            if valuation_diff_percent:
                return valuation_diff_percent / 100
            return NotAvailable

    @pagination
    @method
    class iter_history(TableElement):
        item_xpath = '//table/tbody/tr[contains(@class, "results")]'
        head_xpath = '//table/thead/tr/th'

        col_date = re.compile('Date')
        col_label = re.compile('Libellé')
        col_amount = re.compile('Montant')

        next_page = Link('//a[contains(text(), "Suivant") and not(contains(@href, "javascript"))]', default=None)

        class item(ItemElement):
            klass = Transaction

            obj_raw = Transaction.Raw(TableCell('label'))
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj_amount = CleanDecimal.French(TableCell('amount'), default=NotAvailable)

    @method
    class iter_investment(TableElement):
        item_xpath = '//table/tbody/tr[contains(@class, "results")]'
        head_xpath = '//table/thead/tr/th'

        col_label = re.compile(r'Libellé')
        col_quantity = re.compile(r'Nb parts')
        col_vdate = re.compile(r'Date VL')
        col_unitvalue = re.compile(r'VL')
        col_unitprice = re.compile(r'Prix de revient')
        col_diff_ratio = re.compile(r'Perf\.')
        col_valuation = re.compile(r'Solde')

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_code = IsinCode(Regexp(Link('./td/a'), r'Isin%253D([^%]+)'), default=NotAvailable)
            obj_code_type = IsinType(Regexp(Link('./td/a'), r'Isin%253D([^%]+)'), default=NotAvailable)
            obj_quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)
            obj_unitprice = CleanDecimal.French(TableCell('unitprice'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'), default=NotAvailable)
            obj_valuation = CleanDecimal.French(TableCell('valuation'))
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True, default=NotAvailable)

            def obj_diff_ratio(self):
                diff_ratio_percent = CleanDecimal.French(TableCell('diff_ratio'), default=None)(self)
                if diff_ratio_percent:
                    return diff_ratio_percent / 100
                return NotAvailable


MARKET_ORDER_DIRECTIONS = {
    'A': MarketOrderDirection.BUY,
    'S': MarketOrderDirection.BUY,
    'V': MarketOrderDirection.SALE,
}

MARKET_ORDER_TYPES = {
    'MARCHE': MarketOrderType.MARKET,
    'LIMITE': MarketOrderType.LIMIT,
    'DECLENCH': MarketOrderType.TRIGGER,
}


class MarketPage(LoggedPage, HTMLPage):
    def find_account(self, acclabel, accowner):
        # Depending on what we're fetching (history, invests or orders),
        # the parameter to choose the account has a different name.
        if 'carnetOrdre' in self.url:
            param_name = 'idCompte'
        else:
            param_name = 'indiceCompte'
        # first name and last name may not be ordered the same way on market site...
        accowner = sorted(accowner.lower().split())

        def get_ids(ref, acclabel, accowner, param_name):
            ids = None
            for a in self.doc.xpath('//a[contains(@%s, "%s")]' % (ref, param_name)):
                self.logger.debug("get investment from %s" % ref)
                label = CleanText('.')(a)
                owner = CleanText('./ancestor::tr/preceding-sibling::tr[@class="LnMnTiers"][1]')(a)
                owner = re.sub(r' \(.+', '', owner)
                owner = sorted(owner.lower().split())
                if label == acclabel and owner == accowner:
                    ids = list(
                        re.search(r'%s[^\d]+(\d+).*idRacine[^\d]+(\d+)' % param_name, Attr('.', ref)(a)).groups()
                    )
                    ids.append(CleanText('./ancestor::td/preceding-sibling::td')(a))
                    self.logger.debug("assign value to ids: {}".format(ids))
            return ids

        # Check if history is present
        if CleanText(default=None).filter(self.doc.xpath('//body/p[contains(text(), "indisponible pour le moment")]')):
            return False

        ref = CleanText(self.doc.xpath('//a[contains(@href, "%s")]' % param_name))(self)
        if not ref:
            return get_ids('onclick', acclabel, accowner, param_name)
        else:
            return get_ids('href', acclabel, accowner, param_name)

    def get_account_id(self, acclabel, owner):
        account = self.find_account(acclabel, owner)
        if account:
            return account[2].replace(' ', '')

    def go_account(self, acclabel, owner):
        if 'carnetOrdre' in self.url:
            param_name = 'idCompte'
        else:
            param_name = 'indiceCompte'

        ids = self.find_account(acclabel, owner)
        if not ids:
            return

        form = self.get_form(name="formCompte")
        form[param_name] = ids[0]
        form['idRacine'] = ids[1]
        try:
            return form.submit()
        except ServerError:
            return False

    def go_account_full(self):
        form = self.get_form(name="formOperation")
        form['dateDebut'] = "02/01/1970"
        try:
            return form.submit()
        except ServerError:
            return False

    @method
    class iter_history(TableElement):
        item_xpath = '//table[has-class("domifrontTb")]/tr[not(has-class("LnTit") or has-class("LnTot"))]'
        head_xpath = '//table[has-class("domifrontTb")]/tr[1]/td'

        col_date = re.compile('Date')
        col_label = 'Opération'
        col_code = 'Code'
        col_quantity = 'Quantité'
        col_amount = re.compile('Montant')

        class item(ItemElement):
            klass = Transaction

            obj_label = CleanText(TableCell('label'))
            obj_type = Transaction.TYPE_BANK
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)

            # The amount can be displayed in scientific notation if it's too large.
            # In this case we fetch it in the details page for the transaction.
            obj_amount = CleanDecimal.SI(TableCell('amount'), default=None)
            obj__index = Base(TableCell('date'), Regexp(Attr('.//a', 'onclick'), r"indiceHistorique, '([^,]*)',"))

            obj_investments = Env('investments')

            def parse(self, el):
                i = Investment()
                i.label = Field('label')(self)
                i.code = CleanText(TableCell('code'))(self)
                i.quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)(self)
                i.valuation = Field('amount')(self)
                i.vdate = Field('date')(self)
                self.env['investments'] = [i]

    def go_transaction_detail(self, transaction):
        form = self.get_form(name="formOrdre")
        form.url = '2%s' % Regexp(pattern=r'\/\d([^\/]+)$').filter(self.url)
        form['indiceHistorique'] = transaction._index
        form.submit()

    def get_transaction_amount(self):
        return CleanDecimal.French('//td[contains(text(), "Net client")]/following-sibling::td[1]')(self.doc)

    @method
    class iter_investment(TableElement):
        item_xpath = '//table[has-class("domifrontTb")]/tr[not(has-class("LnTit") or has-class("LnTot"))]'
        head_xpath = '//table[has-class("domifrontTb")]/tr[1]/td'

        col_label = 'Valeur'
        col_code = 'Code'
        col_quantity = 'Qté'
        col_vdate = 'Date cours'
        col_unitvalue = 'Cours'
        col_unitprice = re.compile('P.R.U')
        col_valuation = 'Valorisation'

        class item(ItemElement):
            klass = Investment

            def condition(self):
                return not CleanText('//div[has-class("errorConteneur")]', default=None)(self.el)

            obj_label = Upper(TableCell('label'))
            obj_quantity = CleanDecimal.French(TableCell('quantity'), default=NotAvailable)
            obj_unitprice = CleanDecimal.French(TableCell('unitprice'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'), default=NotAvailable)
            obj_valuation = CleanDecimal.French(TableCell('valuation'))
            obj_vdate = Date(CleanText(TableCell('vdate')), dayfirst=True, default=NotAvailable)

            def obj_code(self):
                if Field('label')(self) == "LIQUIDITES":
                    return 'XX-liquidity'
                return IsinCode(CleanText(TableCell('code')), default=NotAvailable)(self)

            obj_code_type = IsinType(CleanText(TableCell('code')), default=NotAvailable)

    def get_error_message(self):
        return CleanText('//div[has-class("titError") or has-class("TitError")]')(self.doc)

    @method
    class iter_market_orders(TableElement):
        item_xpath = '//table[has-class("domifrontTb")]/tr[not(has-class("LnTit") or has-class("LnTot"))]'
        head_xpath = '//table[has-class("domifrontTb")]/tr[1]/td'

        col_label = 'Valeur'
        col_direction = 'Sens'
        col_state = 'Status'
        col_quantity = 'Qté'
        col_validity_date = 'Validité'

        class item(ItemElement):
            klass = MarketOrder

            obj_label = Regexp(CleanText(TableCell('label')), r'([^\(]*) \(')
            obj_code = IsinCode(Regexp(CleanText(TableCell('label')), r'\(\w+\)'), default=NotAvailable)
            obj_direction = Map(
                CleanText(TableCell('direction')),
                MARKET_ORDER_DIRECTIONS,
                MarketOrderDirection.UNKNOWN
            )
            obj_state = CleanText(TableCell('state'))
            obj_quantity = CleanDecimal.French(TableCell('quantity'), sign='+')
            obj_validity_date = Date(CleanText(TableCell('validity_date')), dayfirst=True, default=NotAvailable)

            obj__index = Base(TableCell('label'), Regexp(Attr('.//a', 'onclick'), r'indiceOrdre, ([^,]*),'))
            obj__type = Base(TableCell('label'), Regexp(Attr('.//a', 'onclick'), r"typeOrdre, '([^']*)'"))

    def go_order_detail(self, order):
        form = self.get_form(name="parametres")
        form.url = '2%s' % Regexp(pattern=r'\/\d([^\/]+)$').filter(self.url)
        form['indiceOrdre'] = order._index
        form['typeOrdre'] = order._type
        form.submit()

    @method
    class fill_market_order(ItemElement):
        obj_id = CleanText(
            '//tr/td[@class="CelTitCol" and contains(text(), "Référence")]/following-sibling::td[1]',
            default=NotAvailable
        )
        obj_order_type = MapIn(
            CleanText('//tr/td[@class="CelTitCol" and contains(text(), "Mention")]/following-sibling::td[1]'),
            MARKET_ORDER_TYPES,
            MarketOrderType.UNKNOWN
        )
        obj_ordervalue = CleanDecimal.French(
            '//tr/td[@class="CelTitCol" and contains(text(), "Cours limite")]/following-sibling::td[1]',
            default=NotAvailable
        )
        obj_date = Date(
            Regexp(
                CleanText(
                    '//tr/td[@class="CelTitCol" and contains(text(), "enregistrement")]/following-sibling::td[1]'
                ),
                r'(.*) à'
            ),
            dayfirst=True
        )
        obj_execution_date = Date(
            Regexp(
                CleanText('//tr/td[@class="CelTitCol" and contains(text(), "exécution")]/following-sibling::td[1]'),
                r'(.*) à',
                default=NotAvailable
            ),
            dayfirst=True,
            default=NotAvailable
        )


class AdvisorPage(LoggedPage, JsonPage):
    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = Dict('nomPrenom')
        obj_email = obj_mobile = NotAvailable

        def obj_phone(self):
            return Dict('numeroTelephone')(self) or NotAvailable

    @method
    class update_agency(ItemElement):
        obj_fax = CleanText(Dict('numeroFax'), replace=[(' ', '')])
        obj_agency = Dict('nom')
        obj_address = Format('%s %s', Dict('adresse1'), Dict('adresse3'))


class ProfilePage(LoggedPage, JsonPage):
    # be careful, this page is used in CmsoProBrowser too!

    @method
    class get_profile(ItemElement):
        klass = Profile

        def obj_id(self):
            return (
                Dict('identifiantExterne', default=None)(self)
                or Dict('login')(self)
            )

        obj_name = Format('%s %s', Dict('firstName'), Dict('lastName'))
        obj_email = Dict('email', default=NotAvailable)  # can be unavailable on pro website for example

    def get_token(self):
        return Dict('loginEncrypted')(self.doc)
