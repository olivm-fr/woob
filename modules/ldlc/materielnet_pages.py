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

from __future__ import unicode_literals


import re

from weboob.browser.pages import HTMLPage, LoggedPage, PartialHTMLPage
from weboob.browser.filters.standard import CleanText, CleanDecimal, Env, Format, Date, Async, Filter, Regexp, Field
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.browser.filters.html import Attr, Link
from weboob.capabilities.bill import DocumentTypes, Bill, Subscription
from weboob.capabilities.base import NotAvailable
from weboob.exceptions import BrowserIncorrectPassword


class LoginPage(PartialHTMLPage):
    def get_recaptcha_sitekey(self):
        return Attr('//div[@class="g-recaptcha"]', 'data-sitekey', default=NotAvailable)(self.doc)

    def login(self, login, password, captcha_response=None):
        maxlength = int(Attr('//input[@id="Email"]', 'data-val-maxlength-max')(self.doc))
        regex = Attr('//input[@id="Email"]', 'data-val-regex-pattern')(self.doc)
        # their regex is: ^([\w\-+\.]+)@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.)|(([\w-]+\.)+))([a-zA-Z]{2,15}|[0-9]{1,3})(\]?)$
        # but it is not very good, we escape - inside [] to avoid bad character range Exception
        regex = regex.replace('[\w-+\.]', '[\w\-+\.]')

        if len(login) > maxlength:  # actually it's 60 char
            raise BrowserIncorrectPassword(Attr('//input[@id="Email"]', 'data-val-maxlength')(self.doc))

        if not re.match(regex, login):
            raise BrowserIncorrectPassword(Attr('//input[@id="Email"]', 'data-val-regex')(self.doc))

        form = self.get_form(xpath='//form[contains(@action, "/Login/Login")]')
        form['Email'] = login
        form['Password'] = password

        if captcha_response:
            form['g-recaptcha-response'] = captcha_response

        form.submit()

    def get_error(self):
        return CleanText('//span[contains(@class, "field-validation-error")]')(self.doc)


class CaptchaPage(HTMLPage):
    def get_error(self):
        return CleanText('//div[@class="captcha-block"]/p[1]/text()')(self.doc)


class ProfilePage(LoggedPage, HTMLPage):
    @method
    class get_subscriptions(ListElement):
        class item(ItemElement):
            klass = Subscription

            obj_subscriber = Format('%s %s', Attr('//input[@id="FirstName"]', 'value'), Attr('//input[@id="LastName"]', 'value'))

            def obj_id(self):
                if 'Materielnet' in self.page.browser.__class__.__name__:
                    filter_id = CleanText('//p[@class="NumCustomer"]/span')
                else:  # ldlc
                    filter_id = Regexp(CleanText('//span[@class="nclient"]'), r'Nº client : (.*)')

                return filter_id(self)
            obj_label = obj_id


class MyAsyncLoad(Filter):
    def __call__(self, item):
        link = self.select(self.selector, item)
        data = {'X-Requested-With': 'XMLHttpRequest'}
        return item.page.browser.async_open(link, data=data) if link else None


class DocumentsPage(LoggedPage, PartialHTMLPage):
    @method
    class get_documents(ListElement):
        item_xpath = '//div[@class="historic-table"]'

        class item(ItemElement):
            klass = Bill

            load_details = Link('.//div[has-class("historic-cell--details")]/a') & MyAsyncLoad

            obj_id = Format('%s_%s', Env('email'), Field('label'))
            obj_url = Async('details') & Link('//a', default=NotAvailable)
            obj_date = Date(CleanText('./div[contains(@class, "date")]'), dayfirst=True)
            obj_format = 'pdf'
            obj_label = Regexp(CleanText('./div[contains(@class, "ref")]'), r' (.*)')
            obj_type = DocumentTypes.BILL
            obj_price = CleanDecimal(CleanText('./div[contains(@class, "price")]'), replace_dots=(' ', '€'))
            obj_currency = 'EUR'

            def parse(self, el):
                self.env['email'] = self.page.browser.username


class DocumentsDetailsPage(LoggedPage, PartialHTMLPage):
    pass
