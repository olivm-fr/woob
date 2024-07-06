# -*- coding: utf-8 -*-

# Copyright(C) 2018      Fong Ngo
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

from decimal import Decimal

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import (
    CleanDecimal, CleanText, Currency, Date, Env, Field, Format, FromTimestamp,
    Map, Title, Type,
)
from woob.browser.pages import HTMLPage, JsonPage, LoggedPage
from woob.capabilities.bank import Transaction
from woob.capabilities.bank.wealth import (
    Investment, MarketOrder, MarketOrderDirection, MarketOrderPayment,
    MarketOrderType,
)
from woob.capabilities.base import NotAvailable, empty
from woob.tools.capabilities.bank.investments import IsinCode, IsinType


class AccountPage(LoggedPage, JsonPage):
    def get_ncontrat(self):
        return self.doc['identifiantContratCrypte']


class PortfolioPage(LoggedPage, JsonPage):
    def get_valuation_diff(self):
        return CleanDecimal(Dict('totalPlv'))(self.doc)  # Plv = plus-value

    def get_date(self):
        # 'dateValo' can come in two different format. Taking June 4, 2021 as an example, we have:
        # 'Fri Jun 04 00:00:00 CEST 2021' or '210604'
        return Date(Dict('dateValo'), yearfirst=True)(self.doc)

    def get_account_currency(self):
        return Currency(Dict('devise'))(self.doc)

    @method
    class iter_investments(DictElement):
        item_xpath = 'listeSegmentation/*'  # all categories are fetched: obligations, actions, OPC

        class item(ItemElement):
            klass = Investment

            def condition(self):
                # Some rows do not contain an expected item format,
                # There is no valuation (mnt) because some buy/sell orders are not yet finished.
                # We want invalid values to fail in the CleanDecimal filter so we catch only when mnt is missing
                return Dict('mnt', default=NotAvailable)(self) is not NotAvailable

            obj_label = CleanText(Dict('libval'))
            obj_code = IsinCode(CleanText(Dict('codval')), default=NotAvailable)
            obj_code_type = IsinType(Field('code'))
            obj_quantity = CleanDecimal.SI(Dict('qttit'))
            obj_valuation = CleanDecimal.SI(Dict('mnt'))
            obj_unitprice = CleanDecimal.SI(Dict('pam', default=None), default=NotAvailable)
            obj_vdate = Env('date')

            def parse(self, el):
                symbols = {
                    '+': 1,
                    '-': -1,
                    '\u0000': None,  # "NULL" character
                }
                self.env['sign'] = symbols.get(Dict('signePlv')(self), None)

            def obj_diff(self):
                if Dict('plv', default=None)(self) and Env('sign')(self):
                    return CleanDecimal.SI(Dict('plv'), sign=lambda x: Env('sign')(self))(self)
                return NotAvailable

            def obj_diff_ratio(self):
                if not Env('sign')(self):
                    return NotAvailable
                # obj_diff_ratio key can have several names:
                # To handle the case of numbers in scientific notation, like -4.0005864849746154e-05
                if Dict('plvPourcentage', default=None)(self):
                    return Type(Format('%s', Dict('plvPourcentage')), type=Decimal)(self) / 100
                elif Dict('pourcentagePlv', default=None)(self):
                    return Type(Format('%s', Dict('pourcentagePlv')), type=Decimal)(self) / 100

            def obj_portfolio_share(self):
                active_percent = CleanDecimal.SI(
                    Dict('pourcentageActif', default=None),
                    default=NotAvailable
                )(self)
                if empty(active_percent):
                    return NotAvailable
                return active_percent / 100

            def obj_original_currency(self):
                currency = Currency(Dict('devcrs'))(self)
                if currency != Env('account_currency')(self):
                    return currency
                return NotAvailable

            def obj_unitvalue(self):
                if Field('original_currency')(self) or Dict('devcrs')(self) == '%':
                    # 'crs' key contains the original_unitvalue
                    # In some cases it contains "%" as the value is displayed as a percentage
                    return NotAvailable
                return CleanDecimal.SI(Dict('crs'))(self)

            def obj_original_unitvalue(self):
                if Field('original_currency')(self):
                    return CleanDecimal.SI(Dict('crs'))(self)
                return NotAvailable


class AccountCodesPage(LoggedPage, JsonPage):
    def is_linebourse_space_available(self):
        # Some market accounts have no Linebourse space on bp website
        return CleanText(Dict('codeRetour', default=''))(self.doc) != 'sessionKO'

    def get_contract_number(self, account_id):
        for acc in self.doc['data']:
            if account_id in acc['affichage']:
                return acc['identifiantContratCrypte']
        raise AssertionError('The account code was not found in the linebourse API.')

    def get_accounts_list(self):
        # Linebourse is not accessible for these accounts
        # Main Browser can catch error like caissedepargne and skip them
        assert self.doc.get('codeRetour') != 'sessionKO', "No linebourse space"
        return [acc['affichage'] for acc in self.doc['data']]


class NewWebsiteFirstConnectionPage(LoggedPage, JsonPage):
    def build_doc(self, content):
        content = JsonPage.build_doc(self, content)
        if 'data' in content:
            # The value contains HTML
            # Must be encoded into str because HTMLPage.build_doc() uses BytesIO
            # which expects bytes
            html_page = HTMLPage(self.browser, self.response)
            return html_page.build_doc(content['data'].encode(self.encoding))
        return content


class HistoryAPIPage(LoggedPage, JsonPage):
    @method
    class iter_history(DictElement):
        item_xpath = 'data/lstOperations'

        class item(ItemElement):
            klass = Transaction

            obj_label = Format(
                '%s %s (%s)',
                CleanText(Dict('libNatureOperation')),
                CleanText(Dict('libValeur')),
                CleanText(Dict('codeValeur'))
            )
            obj_amount = CleanDecimal.SI(Dict('mntNet'))
            obj_date = Date(CleanText(Dict('dtOperation')), dayfirst=True)
            obj_rdate = Date(CleanText(Dict('dtOperation')), dayfirst=True)
            obj_type = Transaction.TYPE_BANK


MARKET_ORDER_DIRECTIONS = {
    'Achat': MarketOrderDirection.BUY,
    'Vente': MarketOrderDirection.SALE,
}

MARKET_ORDER_TYPES = {
    'MO': MarketOrderType.MARKET,  # 'Au marché'
    'LIM': MarketOrderType.LIMIT,  # 'A cours limité'
    'ASD': MarketOrderType.TRIGGER,  # 'A seuil de déclenchement'
    'APD': MarketOrderType.TRIGGER,  # 'A plage de déclenchement'
}

MARKET_ORDER_PAYMENTS = {
    'Comptant': MarketOrderPayment.CASH,
}

STOCK_MARKET_CODES = {
    '44': 'XETRA',
    '54': 'MADRID',
    '65': 'NYSE',
    '67': 'NASDAQ',
    '361': 'LONDON',
}


class MarketOrderPage(LoggedPage, JsonPage):
    @method
    class iter_market_orders(DictElement):
        # Fetch all 'listeSegmentee' categories: DIVERS, INTRODUCTIONS, OPC, ACTIONSOBLIGATIONS.
        item_xpath = 'listeSegmentee/*'

        class item(ItemElement):
            klass = MarketOrder

            obj_id = Dict('referenceOrdre')
            obj_label = Title(Dict('libelleValeur'))
            # For some reason, only the 'quantity' uses the French format in the JSON...
            obj_quantity = CleanDecimal.French(Dict('quantite'))
            obj_unitprice = CleanDecimal.SI(Dict('limiteSeuilCours', default=NotAvailable), default=NotAvailable)
            obj_currency = Currency(Dict('deviseOrdre'))
            obj_state = CleanText(Dict('etat'))
            obj_code = IsinCode(CleanText(Dict('codeValeur')), default=NotAvailable)
            obj_direction = Map(Dict('nature'), MARKET_ORDER_DIRECTIONS, MarketOrderDirection.UNKNOWN)
            obj_payment_method = Map(Dict('typeReglement'), MARKET_ORDER_PAYMENTS, MarketOrderPayment.UNKNOWN)
            # Note: the 'modalite' key can also be an empty string (unknown order type)
            obj_order_type = Map(Dict('modalite'), MARKET_ORDER_TYPES, MarketOrderType.UNKNOWN)
            obj_date = FromTimestamp(Dict('dateOrdre'), millis=True)
            # Validity date is not always available
            obj_validity_date = FromTimestamp(Dict('dateValidite', default=None), millis=True, default=NotAvailable)

            def obj_amount(self):
                if CleanDecimal.SI(Dict('net'))(self) == 0:
                    # Order amount is probably not available yet
                    return NotAvailable
                # For executed orders, the net amount is equal to quantity * unitprice (minus taxes)
                return CleanDecimal.SI(Dict('net'))(self)

            def obj_ordervalue(self):
                if Dict('modalite')(self) == 'MO':
                    return NotAvailable
                if Dict('modalite')(self) == 'LIM':
                    return CleanDecimal.SI(Dict('limite'))(self)
                if Dict('modalite')(self) in ('ASD', 'APD'):
                    return CleanDecimal.SI(Dict('seuil'))(self)

            def obj_stock_market(self):
                raw_market = Dict('idPlace', default=None)(self)
                if not raw_market:
                    return NotAvailable
                stock_market = Map(CleanText(Dict('idPlace')), STOCK_MARKET_CODES, NotAvailable)(self)
                if empty(stock_market):
                    self.logger.warning('A new stock exchange code was identified: %s', raw_market)
                return stock_market
