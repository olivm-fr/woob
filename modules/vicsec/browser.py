# Copyright(C) 2015      Oleg Plakhotniuk
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.


import re
from datetime import datetime
from decimal import Decimal
from itertools import chain

from woob.browser import URL, LoginBrowser, need_login
from woob.browser.pages import HTMLPage
from woob.capabilities.base import Currency
from woob.capabilities.shop import Item, Order, Payment
from woob.exceptions import BrowserIncorrectPassword
from woob.tools.capabilities.bank.transactions import AmericanTransaction as AmTr


__all__ = ["VicSec"]


class VicSecPage(HTMLPage):
    @property
    def logged(self):
        return bool(self.doc.xpath('//a[@href="https://www.victoriassecret.com/account/profile"]'))


class LoginPage(VicSecPage):
    def login(self, email, password):
        form = self.get_form(name="accountLogonForm")
        form["j_username"] = email
        form["j_password"] = password
        form.submit()


class HistoryPage(VicSecPage):
    def iter_orders(self):
        return [onum for date, onum in sorted(chain(self.orders(), self.returns()), reverse=True)]

    def orders(self):
        for tr in self.doc.xpath('//table[@class="order-status"]/tbody[1]/tr'):
            date = datetime.strptime(tr.xpath("td[1]/text()")[0], "%m/%d/%Y")
            num = tr.xpath("td[2]/a/text()")[0]
            status = tr.xpath("td[4]/span/text()")[0]
            if status == "Delivered":
                yield date, num

    def returns(self):
        for tr in self.doc.xpath('//table[@class="order-status"]/tbody[3]/tr'):
            num = tr.xpath("td[1]/a/text()")[0]
            date = datetime.strptime(tr.xpath("td[2]/text()")[0], "%m/%d/%Y")
            status = tr.xpath("td[4]/span/text()")[0]
            if status == "Complete":
                yield date, num


class OrderPage(VicSecPage):
    def order(self):
        order = Order(id=self.order_number())
        order.date = self.order_date()
        order.tax = self.tax()
        order.discount = self.discount()
        order.shipping = self.shipping()
        return order

    def payments(self):
        for tr in self.doc.xpath(
            '//tbody[@class="payment-summary"]' '//th[text()="Payment Summary"]/../../../tbody/tr'
        ):
            method = tr.xpath("td[1]/text()")[0]
            amnode = tr.xpath("td[2]")[0]
            amsign = -1 if amnode.xpath("em") else 1
            amount = amnode.text_content().strip()
            pmt = Payment()
            pmt.date = self.order_date()
            pmt.method = method
            pmt.amount = amsign * AmTr.decimal_amount(amount)
            if pmt.method not in ["Funds to be applied on backorder"]:
                yield pmt

    def items(self):
        for tr in self.doc.xpath('//tbody[@class="order-items"]/tr'):
            label = tr.xpath("*//h1")[0].text_content().strip()
            price = AmTr.decimal_amount(
                re.match(
                    r"^\s*([^\s]+)(\s+.*)?", tr.xpath('*//div[@class="price"]')[0].text_content(), re.DOTALL
                ).group(1)
            )
            url = "http:" + tr.xpath("*//img/@src")[0]
            item = Item()
            item.label = label
            item.url = url
            item.price = price
            yield item

    def is_void(self):
        return not self.doc.xpath('//tbody[@class="order-items"]/tr')

    def order_number(self):
        return self.order_info("Order Number")

    def order_date(self):
        return datetime.strptime(self.order_info("Order Date"), "%m/%d/%Y")

    def tax(self):
        return self.payment_part("Sales Tax")

    def shipping(self):
        return self.payment_part("Shipping & Handling")

    def order_info(self, which):
        info = self.doc.xpath('//p[@class="orderinfo"]')[0].text_content()
        return re.match(".*%s:\\s+([^\\s]+)\\s" % which, info, re.DOTALL).group(1)

    def discount(self):
        # Sometimes subtotal doesn't add up with items.
        # I saw that "Special Offer" was actually added to the item's price,
        # instead of being subtracted. Looks like a bug on VS' side.
        # To compensate for it I'm correcting discount value.
        dcnt = self.payment_part("Special Offer")
        subt = self.payment_part("Merchandise Subtotal")
        rett = self.payment_part("Return Merchandise Total")
        items = sum(i.price for i in self.items())
        return dcnt + subt + rett - items

    def payment_part(self, which):
        # The numbers notation on VS is super wierd.
        # Sometimes negative amounts are represented by <em> element.
        for node in self.doc.xpath('//tbody[@class="payment-summary"]' '//td[contains(text(),"%s")]/../td[2]' % which):
            strv = node.text_content().strip()
            v = Decimal(0) if strv == "FREE" else AmTr.decimal_amount(strv)
            return -v if node.xpath("em") and v > 0 else v
        return Decimal(0)


class VicSec(LoginBrowser):
    BASEURL = "https://www.victoriassecret.com"
    login = URL(r"/account/signin/overlay$", LoginPage)
    history = URL(r"/account/orderlist$", HistoryPage)
    order = URL(
        r"/account/orderstatus/submit/view\?emailId=(?P<email>.*)&orderNumber=(?P<order_num>\d+)$",
        r"/account/orderstatus.*$",
        OrderPage,
    )
    unknown = URL(r"/.*$", VicSecPage)

    def get_currency(self):
        # Victoria's Secret uses only U.S. dollars.
        return Currency.get_currency("$")

    def get_order(self, id_):
        return self.to_order(id_).order()

    def iter_orders(self):
        for order in self.to_history().iter_orders():
            if not self.to_order(order).is_void():
                yield self.page.order()

    def iter_payments(self, order):
        return self.to_order(order.id).payments()

    def iter_items(self, order):
        return self.to_order(order.id).items()

    @need_login
    def to_history(self):
        self.history.stay_or_go()
        assert self.history.is_here()
        return self.page

    @need_login
    def to_order(self, order_num):
        self.order.stay_or_go(order_num=order_num, email=self.username.upper())
        assert self.order.is_here(order_num=order_num, email=self.username.upper())
        return self.page

    def do_login(self):
        self.session.cookies.clear()
        # Need to go there two times. Perhaps because of cookies...
        self.location("/secureoverlay/wrapper/medium/account/signin/overlay/show")
        self.login.go()
        self.login.go().login(self.username, self.password)
        self.history.go()
        if not self.history.is_here():
            raise BrowserIncorrectPassword()
