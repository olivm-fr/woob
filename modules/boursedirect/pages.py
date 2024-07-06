# Copyright(C) 2012-2020  Budget Insight
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

import re

from woob.capabilities.base import NotAvailable
from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import (
    Investment, MarketOrder, MarketOrderDirection,
    MarketOrderType, MarketOrderPayment,
)
from woob.exceptions import BrowserHTTPNotFound
from woob.browser.pages import HTMLPage, JsonPage, RawPage
from woob.browser.filters.html import Attr, TableCell, ReplaceEntities
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import (
    Base, CleanDecimal, CleanText, Coalesce, Currency, Date,
    Eval, Field, Format, Lower, MapIn, QueryValue, Regexp,
)
from woob.browser.filters.html import Link
from woob.browser.elements import method, ListElement, ItemElement, TableElement
from woob.tools.capabilities.bank.investments import (
    is_isin_valid, create_french_liquidity, IsinCode, IsinType,
)


class LoginPage(JsonPage):
    def get_error_401_message(self):
        # Detailed error message that allows us to filter out the error
        # should be in 'fields/errors/1' but this key sometimes does not
        # exist and value is in 0 instead.
        return Coalesce(
            Dict('fields/errors/1', default=''),
            Dict('fields/errors/0', default=''),
            default=''
        )(self.doc)

    def get_error_403_message(self):
        return Dict('error')(self.doc)


class TwofaStatePage(JsonPage):
    def is_device_trusted(self):
        return Dict('device_state')(self.doc) == 'trusted'

    def is_totp_twofa(self):
        # Available twfo methods are TOTP or SMS OTP.
        # Both can be active on one account. If that's
        # the case, the website default behavior seems
        # to be using TOTP (but the user can always
        # click on "changer de méthode"). We follow
        # the same rule. Plus, chosing TOTP first exempts
        # us from using the request to generate and send
        # the OTP, unlike the SMS method.
        for twofa in self.doc['systems']:
            if twofa['enabled'] is True and twofa['type'] == 'totp':
                return True

    def get_mobile_number(self):
        for twofa in self.doc['systems']:
            if twofa['type'] == 'sms':
                return twofa['mobile']


class ValidateTOTPPage(JsonPage):
    def get_error_message(self):
        return Dict('error')(self.doc)


class SendOTPSMSPage(JsonPage):
    pass


class ValidateOTPSMSPage(ValidateTOTPPage):
    pass


class PasswordRenewalPage(HTMLPage):
    def get_message(self):
        return CleanText('//p[@class="auth-intro"]')(self.doc)


class BasePage(HTMLPage):
    @property
    def logged(self):
        return (
            'function setTop(){top.location="/fr/actualites"}' not in self.text
            or CleanText('//body')(self.doc)
        )

    def detect_encoding(self):
        """
        We need to ignore the charset in the HTML document itself,
        as it is lying. And instead, just trust the response content type encoding
        """
        encoding = self.encoding

        if encoding == u'iso-8859-1' or not encoding:
            encoding = u'windows-1252'

        return encoding


class HomePage(BasePage):
    @property
    def logged(self):
        """Check that the content of the page is related to a "Client" and not to a "Visitor"

        Sometimes, the login fails but we are still redirected to the "actualite" home page.
        If the user is properly connected, there will be the client menu on the page.
        So, we can detect if we are logged in based on the existence of the log out link.
        """

        if not super(HomePage, self).logged:
            return False

        if not self.doc.xpath('//a[@href="/fr/deconnexion"][has-class("btn-logout")]'):
            return False
        return True


class AccountsPage(BasePage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//select[@id="nc"]/option'

        class item(ItemElement):
            klass = Account

            text = CleanText('.')

            obj_id = obj_number = Regexp(text, r'^(\w+)')
            obj_label = Regexp(text, r'^\w+ (.*)')
            obj_currency = 'EUR'
            obj__select = Attr('.', 'value')

            def obj_type(self):
                label = Field('label')(self).lower()
                if 'compte titre' in label:
                    return Account.TYPE_MARKET
                elif 'pea' in label:
                    return Account.TYPE_PEA
                return Account.TYPE_UNKNOWN

    @method
    class fill_account(ItemElement):
        obj_balance = CleanDecimal.French('//b[text()="TOTAL"]/ancestor::*[position()=1]/following-sibling::td[1]')


class InvestPage(RawPage):
    def build_doc(self, content):
        return content.decode('latin-1')

    @property
    def logged(self):
        # if it's html, then we're not logged
        return not self.doc.lstrip().startswith('<')

    def iter_investment(self):
        assert self.doc.startswith('message=')

        invests = self.doc.split('|')[1:]

        for part in invests:
            if part == '1':
                continue  # separator line

            info = part.split('#')
            if 'Vente transmise au marché' in info:
                # invest sold or not available yet
                continue

            if info[2] == '&nbsp;':
                # space info[2]: not possessed yet, buy is pending
                # "Achat en liq" means that user is using SRD
                if "Achat en liq" in info[0]:
                    inv = Investment()

                    inv.label = "SRD %s" % self.last_name
                    inv.valuation = CleanDecimal.French().filter(info[6])
                    inv.code = self.last_code
                    yield inv

                self.last_name, self.last_code = info[0], self.get_isin(info)
                continue

            inv = Investment()

            inv.label = info[0]

            # Skip investments that have no valuation yet
            inv.valuation = CleanDecimal.French(default=NotAvailable).filter(info[5])
            if inv.valuation == NotAvailable:
                continue

            inv.quantity = CleanDecimal.French().filter(info[2])

            # we need to check if the investment's currency is GBX
            # GBX is not part of the ISO4217, to handle it, we need to hardcode it
            # first, we check there is a currency string after the unitvalue
            unitvalue_currency = info[4].split()
            if len(unitvalue_currency) > 1:
                # we retrieve the currency string
                currency = unitvalue_currency[1]
                # we check if the currency notation match the Penny Sterling(GBX)
                # example : 1234,5 p
                if currency == 'p':
                    inv.original_currency = 'GBP'
                # if not, we can use the regular Currency filter
                else:
                    inv.original_currency = Currency().filter(info[4])

            # info[4] = '123,45 &euro;' for investments made in euro, so this filter will return None
            if inv.original_currency:
                # if the currency string is Penny Sterling
                # we need to adjust the unitvalue to convert it to GBP
                if currency == 'p':
                    inv.original_unitvalue = CleanDecimal.French().filter(info[4]) / 100
                else:
                    inv.original_unitvalue = CleanDecimal.French().filter(info[4])
            else:
                # if the unitvalue is a percentage we don't fetch it
                if '%' in info[4]:
                    inv.unitvalue = NotAvailable
                else:
                    # info[4] may be empty so we must handle the default value
                    inv.unitvalue = CleanDecimal.French(default=NotAvailable).filter(info[4])

            inv.unitprice = CleanDecimal.French().filter(info[3])
            inv.diff = CleanDecimal.French().filter(info[6])
            inv.diff_ratio = CleanDecimal.French().filter(info[7]) / 100
            if info[9]:
                # portfolio_share value may be empty
                inv.portfolio_share = CleanDecimal.French().filter(info[9]) / 100
            inv.code = self.get_isin(info)
            inv.code_type = IsinType(default=NotAvailable).filter(inv.code)

            self.last_name, self.last_code = inv.label, inv.code
            yield inv

    def get_isin(self, info):
        raw = ReplaceEntities().filter(info[1])
        # Sometimes the ISIN code is already available in the info:
        val = re.search(r'val=([^&]+)', raw)
        code = NotAvailable
        if val and "val=" in raw and is_isin_valid(val.group(1)):
            code = val.group(1)
        else:
            # Otherwise we need another request to get the ISIN:
            m = re.search(r'php([^{]+)', raw)
            if m:
                url = "/priv/fiche-valeur.php" + m.group(1)
                try:
                    isin_page = self.browser.open(url).page
                except BrowserHTTPNotFound:
                    # Sometimes the 301 redirection leads to a 404
                    return code
                # Checking that we were correctly redirected:
                if hasattr(isin_page, 'next_url'):
                    isin_page = self.browser.open(isin_page.next_url()).page

                if "/fr/marche/" in isin_page.url:
                    isin = isin_page.get_isin()
                    if is_isin_valid(isin):
                        code = isin
        return code

    def get_liquidity(self):
        parts = self.doc.split('{')
        valuation = CleanDecimal.French().filter(parts[3])
        return create_french_liquidity(valuation)


class JsRedirectPage(HTMLPage):
    def next_url(self):
        return re.search(r'window.top.location.href = "([^"]+)"', self.text).group(1)


MARKET_ORDER_DIRECTIONS = {
    'Achat': MarketOrderDirection.BUY,
    'Vente': MarketOrderDirection.SALE,
}

MARKET_ORDER_TYPES = {
    'au marché': MarketOrderType.MARKET,
    'cours limité': MarketOrderType.LIMIT,
    'seuil de declcht': MarketOrderType.TRIGGER,
    'plage de declcht': MarketOrderType.TRIGGER,
}

MARKET_ORDER_PAYMENTS = {
    'Cpt': MarketOrderPayment.CASH,
    'SRD': MarketOrderPayment.DEFERRED,
}


class MarketOrdersPage(BasePage):
    @method
    class iter_market_orders(TableElement):
        head_xpath = '//div[div[h6[text()="Ordres en carnet"]]]//table//th'
        item_xpath = '//div[div[h6[text()="Ordres en carnet"]]]//table//tr[position()>1]'
        # <div> is for boursedirect, <td> is for ing
        empty_xpath = '//div|td[text()="Pas d\'ordre pour ce compte"]'

        col_direction = 'Sens'
        col_label = 'Valeur'
        col_quantity = 'Quantité'
        col_ordervalue = 'Limite'
        col_state = 'Etat'
        col_unitvalue = 'Cours Exec'
        col_validity_date = 'Validité'
        col_url = 'Détail'

        class item(ItemElement):
            klass = MarketOrder

            # Extract the ID from the URL (for example detailOrdre.php?cn=<account_id>&ref=<order_id>&...)
            obj_id = QueryValue(Base(TableCell('url'), Link('.//a', default=NotAvailable)), 'ref', default=NotAvailable)
            obj_label = CleanText(TableCell('label'))
            # Catch everything until "( )"
            obj_state = Regexp(
                CleanText(TableCell('state')),
                r'(.*?)(?: \(|$)',
                default=NotAvailable
            )
            obj_quantity = Eval(abs, CleanDecimal.French(TableCell('quantity')))
            obj_ordervalue = CleanDecimal.French(TableCell('ordervalue'), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue'), default=NotAvailable)
            obj_validity_date = Date(CleanText(TableCell('validity_date')), dayfirst=True)
            obj_direction = MapIn(
                CleanText(TableCell('direction')),
                MARKET_ORDER_DIRECTIONS,
                MarketOrderDirection.UNKNOWN
            )
            obj_url = Regexp(
                Base(TableCell('url'), Link('.//a', default=NotAvailable)),
                r"ouvrePopup\('([^']+)",
                default=NotAvailable
            )
            # State column also contains stock_market & payment_method (e.g. "(Cpt NYX)")
            obj_stock_market = Regexp(
                CleanText(TableCell('state')),
                r'\((?:Cpt|SRD) (.*)\)',
                default=NotAvailable
            )
            obj_payment_method = MapIn(
                Regexp(
                    CleanText(TableCell('state')),
                    r'\((.*)\)',
                    default=''
                ),
                MARKET_ORDER_PAYMENTS,
                MarketOrderPayment.UNKNOWN
            )


class MarketOrderDetailsPage(BasePage):
    @method
    class fill_market_order(ItemElement):
        obj_date = Date(
            CleanText('//td[text()="Création"]/following-sibling::td[1]'),
            dayfirst=True,
            default=NotAvailable
        )
        obj_execution_date = Date(
            CleanText('//td[text()="Date exécuté"]/following-sibling::td[1]'),
            dayfirst=True,
            default=NotAvailable
        )
        obj_order_type = MapIn(
            Lower(CleanText('//td[text()="Limite"]/following-sibling::td[1]')),
            MARKET_ORDER_TYPES,
            MarketOrderType.UNKNOWN
        )

        obj_code = IsinCode(
            Regexp(
                CleanText('//td[text()="Valeur"]/following-sibling::td[1]'),
                r"\(([^)]+)",
                default=NotAvailable
            ),
            default=NotAvailable
        )


class HistoryPage(BasePage):
    @method
    class iter_history(TableElement):
        item_xpath = '//table[contains(@class,"datas retour")]//tr[@class="row1" or @class="row2"]'
        head_xpath = '//table[contains(@class,"datas retour")]//th'

        col_rdate = 'Date opération'
        col_date = 'Date affectation'
        col_investment_label = 'Libellé'
        col_label = 'Opération'
        col_investment_quantity = 'Qté'
        col_investment_unitvalue = 'Cours'
        col_amount = 'Montant net'

        class item(ItemElement):
            klass = Transaction

            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)  # Date affectation
            obj_rdate = Date(CleanText(TableCell('rdate')), dayfirst=True)  # Date opération
            obj_label = Format('%s - %s', CleanText(TableCell('investment_label')), CleanText(TableCell('label')))
            obj_amount = CleanDecimal.French(TableCell('amount'))

            def obj_investments(self):
                if CleanDecimal.French(TableCell('unitvalue'), default=None) is None:
                    return NotAvailable

                investment = Investment()
                investment.label = CleanText(TableCell('investment_label'))(self)
                investment.valuation = CleanDecimal.French(TableCell('amount'))(self)
                investment.unitvalue = CleanDecimal.French(
                    TableCell('investment_unitvalue'),
                    default=NotAvailable
                )(self)
                investment.quantity = CleanDecimal.French(TableCell('investment_quantity'), default=NotAvailable)(self)
                return [investment]


class IsinPage(HTMLPage):
    def get_isin(self):
        # For american funds, the ISIN code is hidden somewhere else in the page:
        return (
            CleanText('//div[@class="instrument-isin"]/span')(self.doc)
            or Regexp(
                CleanText('//div[contains(@class, "visible-lg")]//a[contains(@href, "?isin=")]/@href'),
                r'isin=([^&]*)'
            )(self.doc)
        )


class PortfolioPage(BasePage):
    # we don't do anything here, but we might land here from a SSO like ing
    pass
