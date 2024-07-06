# -*- coding: utf-8 -*-

# Copyright(C) 2017      Phyks (Lucas Verney)
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

from urllib.parse import urljoin

from woob.browser.elements import ItemElement, ListElement, method
from woob.browser.filters.html import Attr, CleanHTML, Link, XPathNotFound
from woob.browser.filters.standard import CleanDecimal, CleanText, Date, Format
from woob.browser.pages import HTMLPage
from woob.capabilities.base import NotAvailable, Currency
from woob.capabilities.bill import Bill, Subscription


class LoginPage(HTMLPage):
    def do_login(self, email, password):
        form = self.get_form(xpath='//form[@id="user-login"]')
        form['name'] = email
        form['pass'] = password
        form.submit()


class BillsPage(HTMLPage):
    @method
    class get_subscriptions(ListElement):
        item_xpath = '//div[@id="cusModal"]//div[has-class("cus-detail-wrapper")]'

        class item(ItemElement):
            klass = Subscription

            # TODO: Handle energy type
            obj_label = CleanText(CleanHTML('.'))
            obj_id = Attr('./input', 'value')

    @method
    class get_documents(ListElement):
        item_xpath = '//table[has-class("invoice-table-stickyheader")]/tbody/tr'

        class item(ItemElement):
            klass = Bill

            def condition(self):
                return len(self.el.xpath('./td')) > 3

            obj_id = Attr('./td[3]/span', 'title')
            obj_type = Format(
                '%s - %s',
                CleanText('./td[2]'),
                Attr('./td[1]//img', 'title', default="")
            )
            obj_label = obj_type
            obj_format = 'pdf'
            obj_date = Date(CleanText('./td[4]'))
            obj_price = CleanDecimal('./td[5]', replace_dots=(' ', ','))
            def obj_currency(self):
                return Currency.get_currency(CleanText('./td[5]')(self))
            obj_duedate = Date(CleanText('./td[6]'))

            def obj_url(self):
                try:
                    return urljoin(
                        self.page.browser.BASEURL,
                        Link('./td[8]/a[1]')(self)
                    )
                except XPathNotFound:
                    return NotAvailable
