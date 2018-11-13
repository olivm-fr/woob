# -*- coding: utf-8 -*-

# Copyright(C) 2018      Fong Ngo
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

from weboob.browser.elements import method, DictElement, ItemElement
from weboob.browser.filters.json import Dict
from weboob.browser.filters.standard import (
    CleanText, Date, CleanDecimal, Eval, Field, Env, Regexp,
)
from weboob.browser.pages import JsonPage, HTMLPage, LoggedPage
from weboob.capabilities.bank import Investment
from weboob.capabilities.base import NotAvailable
from weboob.exceptions import ActionNeeded
from weboob.tools.capabilities.bank.investments import is_isin_valid


class AccountPage(LoggedPage, JsonPage):
    def get_ncontrat(self):
        return self.doc['identifiantContratCrypte']


class PortfolioPage(LoggedPage, JsonPage):
    def get_valuation_diff(self):
        return CleanDecimal(Dict('totalPlv'))(self.doc)  # Plv = plus-value

    def get_date(self):
        return Date(Regexp(Dict('dateValo'), r'(\d{2})(\d{2})(\d{2})', '\\3\\2\\1'), dayfirst=True)(self.doc)

    @method
    class iter_investments(DictElement):
        item_xpath = 'listeSegmentation/*'  # all categories are fetched: obligations, actions, OPC

        class item(ItemElement):
            klass = Investment

            obj_label = Dict('libval')
            obj_code = Dict('codval')
            obj_code_type = Eval(
                lambda x: Investment.CODE_TYPE_ISIN if is_isin_valid(x) else NotAvailable,
                Field('code')
            )
            obj_quantity = CleanDecimal(Dict('qttit'))
            obj_unitprice = CleanDecimal(Dict('pam'))
            obj_unitvalue = CleanDecimal(Dict('crs'))
            obj_valuation = CleanDecimal(Dict('mnt'))
            obj_vdate = Env('date')
            obj_portfolio_share = Eval(lambda x: x / 100, CleanDecimal(Dict('pourcentageActif')))

            def parse(self, el):
                symbol = Dict('signePlv')(self)
                assert symbol in ('+', '-'), 'should be either positive or negative'
                self.env['sign'] = 1 if symbol == '+' else -1

            def obj_diff(self):
                return CleanDecimal(Dict('plv'), sign=lambda x: Env('sign')(self))(self)

            def obj_diff_percent(self):
                return CleanDecimal(Dict('plvPourcentage'), sign=lambda x: Env('sign')(self))(self)


class ConfigurationPage(LoggedPage, JsonPage):
    def is_first_connexion(self):
        return self.doc['premiereConnexion']  # either True or False

    def get_contract_number(self):
        return self.doc['idCompteActif']


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

    def has_first_connection_cgu(self):
        # New Espace bourse: user is asked to read some documents during first connection
        message = CleanText('//p[contains(text(), "prendre connaissance")]')(self.doc)
        if message:
            raise ActionNeeded(message)


class HistoryAPIPage(LoggedPage, JsonPage):
    def has_history(self):
        return bool(self.doc['data']['nbTotalValeurs'])