# Copyright(C) 2017      Théo Dorée
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

import ast
import re

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.exceptions import BrowserUnavailable
from woob.browser.filters.html import Attr
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanDecimal, CleanText, Currency, Date, Eval, Field, Regexp
from woob.browser.pages import HTMLPage, JsonPage, LoggedPage, RawPage
from woob.capabilities.bank import Account, Transaction
from woob.capabilities.base import NotAvailable, empty
from woob.tools.json import json


class RejectableHTMLPage(HTMLPage):
    def on_load(self):
        if CleanText('//title[text() = "Request Rejected"]')(self.doc):
            raise BrowserUnavailable("Last request was rejected")


class HomePage(RejectableHTMLPage):
    def get_href_randomstring(self, filename):
        # The filename has a random string like `3eacdd2f` that changes often
        # (at least once a week).
        # We can get this string easily because file path is always like that:
        # `/js/<filename>.<randomstring>.js`
        #
        # We can't use Regexp(Link(..)) because all the links are in the <head>
        # tag on the page. That would require to do something like  `//link[25]`
        # to get the correct link, and if they modify/add/remove one link then the
        # regex is going to crash or give us the wrong result.
        href = re.search(r"/js/%s.(\w+).js" % filename, self.text)
        return href.group(1)


class JsAppPage(RejectableHTMLPage):
    def get_js_randomstring(self, filename):
        # Same as get_href_randomstring, some values have been moved to this js file
        # It constructs the js url so the regex has several matches,
        # we take the first one that isn't just the filename parameter
        matches = re.findall(r'%s:"(\w+?)"' % filename, self.text)
        return next(m for m in matches if m != filename)


class JsParamsPage(RejectableHTMLPage):
    def get_json_content(self):
        json_data = re.search(r"JSON\.parse\('(.*)'\)", self.text)
        return json.loads(json_data.group(1))


class JsUserPage(RawPage):
    def get_json_content(self):
        # The regex below will match the JSON by searching for at least one
        # key in it (code_challenge). This JSON is available only one time in the
        # file, so there is no risk of duplicates.
        json_data = re.search(r"({[^{}]+code_challenge:[^{}]+})", self.text).group(1)

        return parse_js_obj(json_data)


class LoginPage(HTMLPage):
    def get_login_form(self):
        form = self.get_form("//form")
        return form

    def get_recaptcha_site_key(self):
        return Attr('//button[contains(@class, "g-recaptcha")]', "data-sitekey", default=False)(self.doc)

    def get_error_message(self):
        return CleanText('//div[@class="login-page"]/div[@role="alert"]//li')(self.doc)


class AuthorizePage(RejectableHTMLPage):
    pass


class AccountsPage(LoggedPage, JsonPage):
    @method
    class iter_accounts(DictElement):
        item_xpath = "data"

        class item(ItemElement):
            klass = Account

            def condition(self):
                return CleanText(Dict("status"))(self) == "active"

            obj_type = Account.TYPE_CARD
            obj_label = obj_id = obj_number = CleanText(Dict("card_ref"))
            obj_currency = Currency(Dict("balances/0/currency"))
            obj__card_class = CleanText(Dict("class"))
            obj__account_ref = CleanText(Dict("account_ref"))
            # The amount has no `.` or `,` in it. In order to get the amount we have
            # to divide the amount we retrieve by 100 (like the website does).
            obj_balance = Eval(lambda x: x / 100, CleanDecimal.SI(Dict("balances/0/remaining_amount")))
            obj_cardlimit = Eval(
                lambda x: x and x / 100,
                CleanDecimal.SI(Dict("balances/0/daily_remaining_amount", default=NotAvailable), default=NotAvailable),
            )


class TransactionsPage(LoggedPage, JsonPage):
    @method
    class iter_transactions(DictElement):
        item_xpath = "data"

        class item(ItemElement):
            klass = Transaction

            def condition(self):
                return CleanText(Dict("status"))(self) != "failed"

            obj_date = Date(Dict("date"))
            obj_amount = Eval(lambda x: x / 100, CleanDecimal(Dict("amount")))

            def obj_raw(self):
                name = Dict("outlet/name", default=NotAvailable)(self)
                reason = Dict("reason", default=NotAvailable)(self)
                if not empty(name) and ("-" in name or empty(reason)):
                    return CleanText().filter(name)
                return CleanText().filter(reason)

            def obj_label(self):
                raw = Field("raw")(self)
                if "Annulation" in raw:
                    return raw

                # Raw labels can be like this :
                # PASTA ANGERS,FRA
                # O SEIZE - 16 RUE D ALSACE, ANGERS,49100,FRA
                # SFR DISTRIBUTION-23-9.20-0.00-2019
                # The regexp is to get the part with only the name
                # The .strip() is to remove any leading whitespaces due to the ` ?-`
                if "-" not in raw:
                    return Regexp(pattern=r"^([^|]+)").filter(raw)
                return Regexp(pattern=r"([^,-]+)(?: ?-|,).*").filter(raw).strip()

            def obj_type(self):
                if Field("amount")(self) < 0:
                    return Transaction.TYPE_CARD
                elif "Annulation" in Field("label")(self):
                    return Transaction.TYPE_PAYBACK
                return Transaction.TYPE_TRANSFER


# If node, an AST node, contains a string or a number, return
# that. Otherwise, return the node itself.
def get_ast_val(node):
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Str):
        return node.s
    elif isinstance(node, ast.Num):
        return node.n

    return node


# Return a dictionary containing values associated with keys found in
# `input`, a string. `input` looks like a JS literal object.
def parse_js_obj(input):
    node = ast.parse(input).body[0].value
    result = {get_ast_val(k): get_ast_val(v) for k, v in zip(node.keys, node.values)}
    return result
