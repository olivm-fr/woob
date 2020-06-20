# -*- coding: utf-8 -*-

# Copyright(C) 2015      Baptiste Delpey
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

from __future__ import unicode_literals

import re
from datetime import date
from decimal import Decimal

from weboob.exceptions import BrowserPasswordExpired
from weboob.browser.pages import HTMLPage, LoggedPage, pagination
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Field, Env, Format, RawText,
    Eval,
)
from weboob.browser.filters.html import Link, Attr, AbsoluteLink
from weboob.capabilities.bank import Account
from weboob.tools.capabilities.bank.transactions import FrenchTransaction


class HomePage(LoggedPage, HTMLPage):
    def is_corporate(self):
        return bool(self.doc.xpath('//div[@class="marges5"]/h5/a[contains(text(), "CORPORATE")]'))

    def is_password_expired(self):
        return bool(self.doc.xpath('//h1[contains(text(), "Change your password")]'))

    def get_error_msg(self):
        return CleanText('//div[@id="errors"]')(self.doc)


class LoginPage(HTMLPage):
    def login(self, type, username, password):
        form = self.get_form(name='connecterForm')
        form['type'] = type
        form['login'] = username
        form['pwd'] = password[:8]
        form.url = '/ce_internet_public/seConnecter.event.do'
        form.submit()


class ExpandablePage(LoggedPage, HTMLPage):
    def expand(self, account=None, rib=None, company=None):
        form = self.get_form()
        if rib is not None:
            form['ribSaisi'] = rib
        if account is not None:
            form['nomCarteSaisi'] = account.label  # some forms use 'nomCarteSaisi' some use 'titulaireSaisie'
            form['titulaireSaisie'] = account.label
        if company is not None:
            if 'entrepriseSaisie' in form.keys():  # some forms use 'entrepriseSaisie' some use 'entrepriseSaisi'
                form['entrepriseSaisie'] = company
            else:
                form['entrepriseSaisi'] = company
        # needed if coporate titulaire
        form.url = form.url.replace('Appliquer', 'Afficher')
        form.submit()

    def get_rib_list(self):
        return self.doc.xpath('//select[@name="ribSaisi"]/option/@value')


class GetableLinksPage(LoggedPage, HTMLPage):
    def get_link(self, account):
        # FIXME this will probably crash on 'titulaire' space but all credentials are wrong pass so cannot test
        number, holder = account._completeid.split(':')
        el = self.doc.xpath('.//tr[.//a[text()=$card]][.//td[1][text()=$name]]//a', card=number, name=holder)
        if not el:
            return
        return el[0].get("href")


class PeriodsPage(LoggedPage, HTMLPage):
    def get_periods(self):
        periods = []
        for period in self.doc.xpath('//select[@name="periodeSaisie"]/option/@value'):
            periods.append(period)
        return periods

    def expand(self, period, account=None, rib=None, company=None):
        form = self.get_form(submit='//input[@value="Display"]')
        if account is not None:
            form['nomCarteSaisi'] = account.label
            form['titulaireSaisi'] = account.label
        form['periodeSaisie'] = period
        if rib is not None:
            form['ribSaisi'] = rib
        if company is not None:
            form['entrepriseSaisi'] = company
        # needed if coporate titulaire
        form.url = form.url.replace('Appliquer', 'Afficher')
        form.submit()


class AccountsPage(ExpandablePage, GetableLinksPage):
    @pagination
    @method
    class iter_accounts(ListElement):
        item_xpath = '//table[@id="datas"]/tbody/tr'

        next_page = Link('//table[@id="datas"]/tfoot//b/following-sibling::a[1]')

        ignore_duplicate = True

        class item(ItemElement):
            klass = Account

            obj_id = CleanText('./td[2]')

            # Some account names have spaces in the middle which cause
            # the history search to fail if we remove them.
            # eg: `NAME  SURNAME` = `NAME++SURNAME` in the history search.
            obj_label = Eval(lambda x: x.strip(), RawText('./td[1]'))
            obj_type = Account.TYPE_CARD
            obj__rib = Env('rib')
            obj__company = Env('company', default=None)  # this field is something used to make the module work, not something meant to be displayed to end users
            obj_currency = 'EUR'
            obj_number = CleanText('./td[2]', replace=[(' ', '')])
            obj_url = AbsoluteLink('./td[2]/a')

            obj__completeid = Format('%s:%s', obj_id, obj_label)

        def store(self, obj):
            return obj

    def get_companies(self):
        return self.doc.xpath('//select[@name="entrepriseSaisie"]/option/@value')


class ComingPage(ExpandablePage):
    def get_link(self, account):
        # FIXME this will probably crash on 'titulaire' space but all credentials are wrong pass so cannot test
        card, holder = account._completeid.split(':')
        el = self.doc.xpath('.//tr[.//a[text()=$card]][.//td[1][text()=$name]]//a', card=card, name=holder)
        if not el:
            return
        link = re.search(r",'(.*)'\);", el[0].get("href"))
        if link:
            return link.group(1)

    def get_balance(self, account):
        # TODO find how pagination works on this page and find account with pagination
        card, holder = account._completeid.split(':')
        el = self.doc.xpath('.//tr[.//a[text()=$card]][.//td[1][text()=$name]]/td[4]', card=card, name=holder)
        if not el:
            return
        return CleanDecimal('.', replace_dots=(',', '.'))(el[0])


class HistoPage(GetableLinksPage, PeriodsPage):
    pass


class TransactionsPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class get_history(ListElement):
        item_xpath = '(//table[contains(@id, "datas")]/tbody/tr | //table[contains(@id, "datas")]//tr[@class])'
        next_page = Link('(//table[@id="tgDecorationTableFoot"] | //table[@id="datas"]/tfoot)//b/following-sibling::a[1]')

        class item(ItemElement):
            klass = FrenchTransaction

            obj_rdate = FrenchTransaction.Date(CleanText('./td[1]'))
            obj_date = FrenchTransaction.Date(CleanText('./td[3]'))
            obj_raw = FrenchTransaction.Raw(CleanText('./td[2]'))
            _obj_amnt = FrenchTransaction.Amount(CleanText('./td[5]'), replace_dots=False)
            obj_original_amount = FrenchTransaction.Amount(CleanText('./td[4]'), replace_dots=False)
            obj_original_currency = FrenchTransaction.Currency(CleanText('./td[4]'))
            obj_commission = FrenchTransaction.Amount(CleanText('./td[6]'), replace_dots=False)

            def obj__coming(self):
                if Field('date')(self) >= date.today():
                    return True

            def obj_amount(self):
                if not Field('obj_commission'):
                    return Field('_obj_amnt')
                else:
                    return CleanDecimal(replace_dots=False).filter(self.el.xpath('./td[5]')) - CleanDecimal(replace_dots=False).filter(self.el.xpath('./td[6]'))


class ErrorPage(HTMLPage):
    def on_load(self):
        msg = CleanText('//div[@id="errors"]')(self.doc)
        if msg == 'Your password has expired: you must change it.':
            raise BrowserPasswordExpired(msg)


class TiCardPage(ExpandablePage, TransactionsPage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//table[@class="params"]/tr//option'

        class item(ItemElement):
            klass = Account
            obj_id = CleanText('.', replace=[(' ', '')])
            obj_label = Format('%s %s', CleanText('//table[@class="params"]/tr/td[1]/b[2]'), Field('id'))
            obj_type = Account.TYPE_CARD
            obj__nav_num = Attr('.', 'value')
            obj_currency = 'EUR'
            obj__company = Env('company', default=None)  # this field is something used to make the module work, not something meant to be displayed to end users

    def get_balance(self):
        if self.doc.xpath('//div[@class="messageaucunedonnee"]'):
            return Decimal(0)
        return CleanDecimal('//div[@class="titre-datas"][1]/b')(self.doc)


class TiHistoPage(PeriodsPage, TransactionsPage):
    pass
