# -*- coding: utf-8 -*-

# Copyright(C) 2015      Vincent Paredes
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

from weboob.browser.pages import HTMLPage, LoggedPage, PartialHTMLPage
from weboob.browser.filters.standard import (
    CleanDecimal, CleanText, Env, Format,
    QueryValue, Currency, Regexp, Async, Date, Field,
    Filter,
)
from weboob.browser.elements import ListElement, ItemElement, method, TableElement
from weboob.browser.filters.html import Attr, Link, TableCell
from weboob.capabilities import NotAvailable
from weboob.capabilities.bill import Bill, Subscription, DocumentTypes
from weboob.tools.date import parse_french_date


class MyAsyncLoad(Filter):
    def __call__(self, item):
        link = self.select(self.selector, item)
        data = {'X-Requested-With': 'XMLHttpRequest'}
        return item.page.browser.async_open(link, data=data) if link else None


class HiddenFieldPage(HTMLPage):
    def get_ctl00_actScriptManager_HiddenField(self):
        param = QueryValue(Attr('//script[contains(@src, "js/CombineScriptsHandler.ashx?")]', 'src'), "_TSM_CombinedScripts_")(self.doc)
        return param


class HomePage(LoggedPage, HTMLPage):
    @method
    class get_subscriptions(ListElement):
        item_xpath = '//div[@id="divAccueilInformationClient"]//div[@id="divInformationClient"]'

        class item(ItemElement):
            klass = Subscription

            obj_subscriber = CleanText('.//div[@id="divlblTitleFirstNameLastName"]//span')
            obj_id = CleanText('.//span[2]')
            obj_label = CleanText('.//div[@id="divlblTitleFirstNameLastName"]//span')


class LoginPage(HTMLPage):
    def get_recaptcha_sitekey(self):
        return Attr('//div[@class="g-recaptcha"]', 'data-sitekey', default=NotAvailable)(self.doc)

    def login(self, username, password, captcha_response=None):
        form = self.get_form(id='aspnetForm')
        form['__EVENTTARGET'] = 'ctl00$cphMainContent$butConnexion'
        form['ctl00$cphMainContent$txbMail'] = username
        form['ctl00$cphMainContent$txbPassword'] = password

        # remove this, else error message will be empty if there is a wrongpass
        del form['ctl00$SaveCookiesChoices']
        if captcha_response:
            form['g-recaptcha-response'] = captcha_response

        form.submit()

    def get_error(self):
        return CleanText('//span[contains(text(), "Identifiants incorrects")]')(self.doc)


class DocumentsPage(LoggedPage, PartialHTMLPage):
    @method
    class get_documents(ListElement):
        item_xpath = '//div[@class="dsp-row"]'

        class item(ItemElement):
            klass = Bill

            load_details = Link('.//a[contains(text(), "D??tails")]') & MyAsyncLoad

            obj_id = Format('%s_%s', Env('subid'), Field('label'))
            obj_url = Async('details') & Link('//a[span[contains(text(), "T??l??charger la facture")]]', default=NotAvailable)
            obj_date = Date(CleanText('./div[contains(@class, "cell-date")]'), dayfirst=True)
            obj_format = 'pdf'
            obj_label = Regexp(CleanText('./div[contains(@class, "cell-nb-order")]'), r' (.*)')
            obj_type = DocumentTypes.BILL
            obj_price = CleanDecimal(CleanText('./div[contains(@class, "cell-value")]'), replace_dots=(' ', '???'))
            obj_currency = 'EUR'


class BillsPage(LoggedPage, HiddenFieldPage):
    def get_range(self):
        elements = self.doc.xpath('//select[@id="ctl00_cphMainContent_ddlDate"]/option')
        # theses options can be:
        # * Depuis les (30|60|90) derniers jours
        # * 2020
        # * 2019
        # * etc...
        # we skip those which contains 'derniers jours' because they also contains the rest of bills,
        # and we don't want duplicate them
        for element in elements:
            if 'derniers jours' in CleanText('.')(element):
                continue
            yield Attr('.', 'value')(element)


class ProBillsPage(BillsPage):
    def get_view_state(self):
        m = re.search(r'__VIEWSTATE\|(.*?)\|', self.text)
        return m.group(1)

    @method
    class iter_documents(TableElement):
        ignore_duplicate = True
        item_xpath = '//table[@id="TopListing"]/tr[contains(@class, "rowTable")]'
        head_xpath = '//table[@id="TopListing"]/tr[@class="headTable"]/td'

        col_id = 'N?? de commande'
        col_date = 'Date'
        col_price = 'Montant HT'

        class item(ItemElement):
            klass = Bill

            obj_id = Format('%s_%s', Env('subid'), CleanText(TableCell('id')))
            obj_url = '/Account/CommandListingPage.aspx'
            obj_format = 'pdf'
            obj_price = CleanDecimal.French(TableCell('price'))
            obj_currency = Currency(TableCell('price'))

            def obj_date(self):
                return parse_french_date(CleanText(TableCell('date'))(self)).date()
