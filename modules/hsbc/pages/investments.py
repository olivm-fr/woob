# coding: utf-8
# Copyright(C) 2012-2020  Budget Insight
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
from __future__ import division

import datetime
import json
import time

from weboob.capabilities import NotAvailable
from weboob.capabilities.bank import Account
from weboob.capabilities.wealth import Investment
from weboob.tools.capabilities.bank.investments import is_isin_valid

from weboob.browser.elements import ItemElement, TableElement, DictElement, method
from weboob.browser.pages import HTMLPage, JsonPage, LoggedPage
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Regexp, Currency, Field, Env,
)
from weboob.browser.filters.html import TableCell, Link
from weboob.browser.filters.json import Dict
from weboob.browser.filters.javascript import JSVar
from weboob.exceptions import BrowserUnavailable


class LogonInvestmentPage(LoggedPage, HTMLPage):
    """Transient page to the real application page."""
    SESSION_INFO = {}

    def on_load(self):
        _, app_data = self.get_session_storage()
        self.SESSION_INFO['app_location'] = JSVar(var='window.location').filter(self.content.decode())
        self.SESSION_INFO['app_data'] = app_data
        self.browser.SESSION_INFO = self.SESSION_INFO

    def is_here(self):
        return 'appPage.min.html' in self.content.decode('iso-8859-1')

    def get_session_storage(self):
        sessionContent = Regexp(
            CleanText('//script[@type="text/javascript"]'),
            r'sessionStorage.setItem\((.*)\)'
        )(self.doc)
        key, value = map(lambda x: x.strip("'").strip(), sessionContent.split(",", 1))
        return key, json.decoder.JSONDecoder().decode(value)


class ProductViewHelper():
    URL = 'https://investissements.clients.hsbc.fr/cwd/group-wd-gateway-war/gateway/wd/RetrieveCustomerPortfolio'

    def __init__(self, browser):
        self.browser = browser

    def raw_post_data(self):
        null = None
        return {
            "aggregateXRaySegmentFilter": [],
            "businessOpUnit": "141",
            "cacheRefreshIndicator": null,
            "functionIndicator": [
                {"functionMessageTriggerDescription": "MyPortfolio-MyHoldings|R01"},
            ],
            "holdingAccountInformation": {
                "accountFilterIndicator": "N",
                "accountFilterRefreshIndicator": "Y",
                "cacheRefreshIndicator": "Y",
                "holdingGroupingViewConfig": "ASSETTYPE",
                "investmentHistoryRequestTypeCode": "CURR",
                "priceQuoteTypeCode": "Delay",
                "productDashboardTypeInformation": [
                    {"productDashboardTypeCode": "EQ"},
                    {"productDashboardTypeCode": "BOND"},
                    {"productDashboardTypeCode": "MNYUT"},
                    {"productDashboardTypeCode": "DIVUT"},
                    {"productDashboardTypeCode": "EURO"},
                    {"productDashboardTypeCode": "SI"},
                    {"productDashboardTypeCode": "FCPI"},
                    {"productDashboardTypeCode": "SCPI"},
                    {"productDashboardTypeCode": "ALT"},
                    {"productDashboardTypeCode": "LCYDEP"},
                    {"productDashboardTypeCode": "FCYDEP"},
                    {"productDashboardTypeCode": "INVTINSUR"},
                    {"productDashboardTypeCode": "NONINVTINSUR"},
                    {"productDashboardTypeCode": "LOAN"},
                    {"productDashboardTypeCode": "MORTGAGE"},
                    {"productDashboardTypeCode": "CARD"},
                    {"productDashboardTypeCode": "UWCASH"},
                ],
                "transactionRangeStartDate": null,
                "watchListFilterIndicator": "N",
            },
            "holdingSegmentFilter": [],
            "orderStatusFilter": [
                {"orderStatusGroupIdentifier": "HOLDING", "productCode": null, "productDashboardTypeCode": null},
                {"orderStatusGroupIdentifier": "PENDING", "productCode": null, "productDashboardTypeCode": null},
            ],
            "paginationRequest": [],
            "portfolioAnalysisFilter": [],
            "segmentFilter": [
                {"dataSegmentGroupIdentifier": "PRTFDTLINF"},
                {"dataSegmentGroupIdentifier": "PORTFTLINF"},
                {"dataSegmentGroupIdentifier": "ACCTGRPINF"},
                {"dataSegmentGroupIdentifier": "ACCTFLTINF"},
            ],
            "sortingCriterias": [],
            "staffId": null,
            "watchlistFilter": [],
        }

    def investment_list_post_data(self):
        raw_data = self.raw_post_data()
        raw_data.pop('aggregateXRaySegmentFilter')
        raw_data.pop('holdingSegmentFilter')
        raw_data.pop('portfolioAnalysisFilter')
        raw_data.pop('watchlistFilter')
        raw_data.pop('cacheRefreshIndicator')
        raw_data.update({
            "functionIndicator": [
                {"functionMessageTriggerDescription": "MyPortfolio-MyHoldings"},
            ],
            "holdingAccountInformation": {
                "accountFilterIndicator": "N",
                "accountFilterRefreshIndicator": "N",
                "cacheRefreshIndicator": "N",
                "holdingGroupingViewConfig": "ASSETTYPE",
                "investmentHistoryRequestTypeCode": "CURR",
                "priceQuoteTypeCode": "Delay",
                "productDashboardTypeInformation": [
                    {"productDashboardTypeCode": "EQ"},
                ],
                "watchListFilterIndicator": "N",
            },
            "orderStatusFilter": [
                {"orderStatusGroupIdentifier": "HOLDING"},
                {"orderStatusGroupIdentifier": "PENDING"},
            ],
            "segmentFilter": [
                {"dataSegmentGroupIdentifier": "HLDORDRINF"},
                {"dataSegmentGroupIdentifier": "HLDGSUMINF"},
            ],
            "sortingCriterias": [
                {"sortField": "PROD-DSHBD-TYP-CDE", "sortOrder": "+"},
                {"sortField": "PRD-DSHBD-STYP-CDE", "sortOrder": "+"},
                {"sortField": "PROD-SHRT-NAME", "sortOrder": "+"},
            ],
        })
        return raw_data

    def liquidity_list_post_data(self):
        base_data = self.investment_list_post_data()
        base_data.update({
            "segmentFilter": [{"dataSegmentGroupIdentifier": "HLDORDRINF"}],
            "sortingCriterias": [
                {"sortField": "ACCT-NUM", "sortOrder": "+"},
                {"sortField": "ACCT-PROD-TYPE-STR", "sortOrder": "+"},
                {"sortField": "CCY-PROD-CDE", "sortOrder": "+"},
                {"sortField": "PROD-MTUR-DT", "sortOrder": "+"},
            ],
        })
        base_data['holdingAccountInformation']['productDashboardTypeInformation'] = [
            {"productDashboardTypeCode": "UWCASH"},
        ]
        return base_data

    def build_request(self, kind=None):
        return dict(
            url=self.URL,
            data=self.build_request_data(kind=kind),
            headers=self.build_request_headers(),
            cookies=self.build_request_cookies(),
        )

    def build_request_headers(self):
        xsrf_token = self.browser.session.cookies['XSRF-TOKEN']
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "Accept-Encoding": "gzip, deflate, br",
            'Accept': '*/*',
            "Connection": "keep-alive",
            "X-HDR-App-Role": "ALL",
            "X-HDR-Target-Function": "currentholdings",
            'X-XSRF-TOKEN': xsrf_token,
        }

    def build_request_cookies(self):
        mandatory_cookies = {
            'opt_in_status': "1",
            'CAMToken': self.browser.session.cookies.get('CAMToken', domain='.investissements.clients.hsbc.fr'),
        }
        for key in ('JSESSIONID', 'XSRF-TOKEN', 'WEALTH-FR-CUST-PORTAL-COOKIE'):
            value = self.browser.session.cookies.get(key, domain='investissements.clients.hsbc.fr')
            assert value, key + " cookie is not set"
            mandatory_cookies.update({key: value})

        return mandatory_cookies

    def build_request_data(self, kind=None):
        d = self.browser.SESSION_INFO['app_data'].get('data')
        assert d, 'No Session Data to perform a request'
        localeCode = '_'.join((d['localeLanguage'], d['localeCountry']))
        holdingAccountInformation = {
            'customerNumber': d['customerID'],
            'localeLocalCode': localeCode,
            'transactionRangeEndDate': int(time.time() * 1000),
        }
        baseHeader = {
            'sessionId': d['sessionID'],
            'userDeviceId': d['userDeviceID'],
            'userId': d['userId'],
        }
        request_data = {
            'channelId': d['channelID'],
            'countryCode': d['customerCountryCode'],
            'customerNumber': d['customerID'],
            'frameworkHeader': {
                'customerElectronicBankingChangeableIdentificationNumber': d['userId'],
                'customerElectronicBankingIdentificationNumber': d['userId'],
            },
            'groupMember': d['customerGroupMemberID'],
            'lineOfBusiness': d['customerBusinessLine'],
            'localeCode': localeCode,
            'swhcbApplicationHeader': {
                'hubUserId': d['userLegacyID'],
                'hubWorkstationId': d['userLegacyDeviceID'],
            },
        }
        if kind == 'account_list':
            holdingAccountInformation.update(self.raw_post_data()['holdingAccountInformation'])
            request_data.update(self.raw_post_data())
        elif kind == 'investment_list' or kind == 'liquidity_list':
            """ Build request data to fetch the list of investments """
            request_data.pop("localeCode")

            if kind == 'investment_list':
                holdingAccountInformation.update(self.investment_list_post_data()['holdingAccountInformation'])
                request_data.update(self.investment_list_post_data())

            elif kind == 'liquidity_list':
                holdingAccountInformation.update(self.liquidity_list_post_data()['holdingAccountInformation'])
                request_data.update(self.liquidity_list_post_data())

            if 'req_id' in self.browser.SESSION_INFO:  # update request identification number
                holdingAccountInformation['requestIdentificationNumber'] = self.browser.SESSION_INFO['req_id']

        else:
            raise NotImplementedError()

        # set up common keys for the request
        request_data['holdingAccountInformation'] = holdingAccountInformation
        request_data['baseHeader'] = baseHeader

        return request_data

    def retrieve_products(self, kind=None):
        """ Build the request from scratch according to 'kind' parameter """
        req = self.build_request(kind=kind)
        # self.browser.location(self.browser.SESSION_INFO['app_location'])
        # cookies may be optionals but headers are mandatory.
        self.browser.location(req['url'], json=req['data'], headers=req['headers'], cookies=req['cookies'])
        self.browser.SESSION_INFO['req_id'] = self.browser.response.json()['sessionInformation']['requestIdentificationNumber']

    def retrieve_invests(self):
        self.retrieve_products(kind='investment_list')
        # Invest account can not have invests
        if self.browser.retrieve_useless_page.is_here():
            return []
        assert isinstance(self.browser.page, RetrieveInvestmentsPage)

        # Invest can have under invest
        investments = []
        for index, invest in enumerate(self.browser.page.iter_investments()):
            if invest._under_invests_number > 1:
                for under_invest in self.browser.page.iter_under_investments(index=index):
                    under_invest.label = invest.label
                    under_invest.code = invest.code
                    under_invest.code_type = invest.code_type
                    under_invest.vdate = invest.vdate
                    investments.append(under_invest)
            else:
                investments.append(invest)
        return investments

    def retrieve_liquidity(self):
        self.retrieve_products(kind='liquidity_list')
        if self.browser.retrieve_useless_page.is_here():
            return []
        assert isinstance(self.browser.page, RetrieveLiquidityPage)
        return self.browser.page.iter_liquidity()

    def retrieve_accounts(self):
        self.retrieve_products(kind='account_list')
        assert isinstance(self.browser.page, RetrieveAccountsPage)
        return self.browser.page.iter_accounts()


class RetrieveAccountsPage(LoggedPage, JsonPage):

    def is_here(self):
        # We should never have both informations at the same time
        is_holding_order_information = bool(self.response.json()['holdingOrderInformation'])
        is_account_filter_information = bool(self.response.json()['accountFilterInformation'])
        return (
            (is_holding_order_information != is_account_filter_information)
            and self.response.json()['accountFilterInformation']
        )

    @method
    class iter_accounts(DictElement):
        TYPE_ACCOUNTS = {
            'SEC': Account.TYPE_MARKET,  # also PEA type
            'CHK': Account.TYPE_CHECKING,
            'INV': Account.TYPE_LIFE_INSURANCE,
            'SAV': Account.TYPE_SAVINGS,
            'MTG': Account.TYPE_MORTGAGE,
            'LNS': Account.TYPE_LOAN,
        }

        # Contains information for all accounts (except defered cards)
        item_xpath = 'accountFilterInformation'

        class item(ItemElement):
            klass = Account

            def obj_id(self):
                acc_id = CleanText(Dict('accountNumber'))(self)
                return acc_id.split(' ')[0]

            def obj_number(self):
                if Dict('accountListInformation')(self):
                    for el in Dict('accountListInformation')(self):
                        if Dict('groupMemberInvestmentAccountCode')(el):
                            # Required to map liquidities to accounts
                            return Dict('accountNumber')(el)
                return Field('id')(self)

            def obj_type(self):
                return self.parent.TYPE_ACCOUNTS.get(Dict('accountTypeCode')(self))

            obj_currency = Currency(Dict('currencyAccountCode'))
            obj_balance = CleanDecimal(
                Dict('accountFilterMultipleCurrencyInformation/0/accountMarketValueAmount')
            )


class RetrieveInvestmentsPage(LoggedPage, JsonPage):

    def is_here(self):
        is_holding_order_information = bool(self.response.json()['holdingOrderInformation'])
        is_account_filter_information = bool(self.response.json()['accountFilterInformation'])
        return (
            (is_holding_order_information != is_account_filter_information)
            and bool(self.response.json()['holdingOrderInformation'])
            and self.response.json()['holdingOrderInformation'][0]['accountTypeCode'] != 'OTH'
        )

    @method
    class iter_investments(DictElement):
        item_xpath = 'holdingOrderInformation'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(Dict('productName'))
            obj_quantity = CleanDecimal(Dict('holdingDetailInformation/0/productHoldingQuantityCount'))
            obj_code = CleanText(Dict('productIdInformation/0/productAlternativeNumber'), replace=[('-FR', '')])

            def obj_code_type(self):
                if is_isin_valid(Field('code')(self)):
                    return Investment.CODE_TYPE_ISIN
                return NotAvailable

            def obj_vdate(self):
                vdate = Dict('holdingDetailInformation/0/productPriceUpdateDate')(self)
                # vdate can be 'None'
                if vdate:
                    return datetime.datetime.fromtimestamp(int(vdate) / 1000).date()
                return NotAvailable

            obj_diff = CleanDecimal(
                Dict(
                    'holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/0/profitLossUnrealizedAmount'
                ),
                default=NotAvailable
            )
            obj_unitprice = CleanDecimal(
                Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/0/productHoldingUnitCostAverageAmount'),
                default=NotAvailable
            )
            obj_unitvalue = CleanDecimal(Dict('holdingDetailInformation/0/productMarketPriceAmount'))
            obj_valuation = CleanDecimal(
                Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/0/productHoldingMarketValueAmount'),
                default=NotAvailable
            )

            def obj_diff_ratio(self):
                ratio = CleanDecimal(
                    Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/0/profitLossUnrealizedPercent'),
                    default=NotAvailable
                )(self)
                if ratio is not NotAvailable:
                    ratio /= 100
                return ratio

            obj_portfolio_share = NotAvailable  # must be computed from the sum of iter_investments

            def obj_original_currency(self):
                currency_text = Dict(
                    'holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/currencyProductHoldingBookValueAmountCode'
                )(self)
                if currency_text:
                    return Currency().filter(currency_text)
                else:
                    return NotAvailable

            obj_original_valuation = CleanDecimal(
                Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/productHoldingBookValueAmount'),
                default=NotAvailable
            )
            obj_original_unitvalue = CleanDecimal(Dict('holdingDetailInformation/0/productMarketPriceAmount'))
            obj_original_unitprice = CleanDecimal(
                Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/productHoldingUnitCostAverageAmount'),
                default=NotAvailable
            )
            obj_original_diff = CleanDecimal(
                Dict(
                    'holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/profitLossUnrealizedAmount'
                ),
                default=NotAvailable
            )

            def obj__invest_account_id(self):
                invest_account_id = CleanText(
                    Dict('holdingSummaryInformation/0/accountNumber')
                )(self)
                return invest_account_id.split(' ')[0]

            def obj__under_invests_number(self):
                return len(Dict('holdingSummaryInformation')(self))

    @method
    class iter_under_investments(DictElement):
        def parse(self, el):
            self.item_xpath = 'holdingOrderInformation/' + str(Env('index')(self)) + '/holdingSummaryInformation'

        class item(ItemElement):
            klass = Investment

            def obj__invest_account_id(self):
                invest_account_id = CleanText(Dict('accountNumber'))(self)
                return invest_account_id.split(' ')[0]

            obj_quantity = CleanDecimal(Dict('productHoldingQuantityCount'))
            obj_unitvalue = CleanDecimal(Dict('holdingMarketPriceAmount'))
            obj_original_currency = Dict('currencyHoldingMarketPriceCode')
            obj_valuation = CleanDecimal(Dict(
                'holdingSummaryMultipleCurrencyInformation/0/productHoldingMarketValueAmount'
            ))
            obj_original_valuation = CleanDecimal(
                Dict('holdingSummaryMultipleCurrencyInformation/0/productHoldingBookValueAmount'),
                default=NotAvailable
            )
            obj_unitprice = CleanDecimal(
                Dict('holdingSummaryMultipleCurrencyInformation/0/productHoldingUnitCostAverageAmount'),
                default=NotAvailable
            )

            def obj_diff_ratio(self):
                ratio = CleanDecimal(
                    Dict('holdingSummaryMultipleCurrencyInformation/0/profitLossUnrealizedPercent'),
                    default=NotAvailable
                )(self)
                if ratio is not NotAvailable:
                    ratio /= 100
                return ratio

            obj_diff = CleanDecimal(
                Dict('holdingSummaryMultipleCurrencyInformation/0/profitLossUnrealizedAmount'),
                default=NotAvailable
            )


class RetrieveLiquidityPage(LoggedPage, JsonPage):

    def is_here(self):
        is_holding_order_information = bool(self.response.json()['holdingOrderInformation'])
        is_account_filter_information = bool(self.response.json()['accountFilterInformation'])
        return (
            (is_holding_order_information != is_account_filter_information)
            and bool(self.response.json()['holdingOrderInformation'])
            and self.response.json()['holdingOrderInformation'][0]['accountTypeCode'] == 'OTH'
        )

    @method
    class iter_liquidity(DictElement):
        item_xpath = 'holdingOrderInformation'

        class item(ItemElement):
            klass = Investment

            def condition(self):
                return Dict('productTypeCode')(self) == 'INVCASH'

            obj_label = "Liquidités"
            obj_code = "XX-liquidity"
            obj_code_type = NotAvailable
            obj_valuation = CleanDecimal(Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/productHoldingMarketValueAmount'))
            obj_original_currency = Currency(Dict('holdingDetailInformation/0/holdingDetailMultipleCurrencyInformation/1/currencyProductHoldingMarketValueAmountCode'))

            obj__invest_account_id = CleanText(Dict('productAlternativeNumber'))


class RetrieveUselessPage(LoggedPage, JsonPage):
    def is_here(self):
        is_holding_order_information = bool(self.response.json()['holdingOrderInformation'])
        is_account_filter_information = bool(self.response.json()['accountFilterInformation'])
        # We should never have both informations at the same time
        assert not is_holding_order_information
        return is_holding_order_information == is_account_filter_information

    def on_load(self):
        # Invest account is sometime not available
        if self.response.json()['responseCode'] == "004":
            raise BrowserUnavailable()

        assert self.response.json()['responseCode'] == "000"


class ScpiInvestmentPage(LoggedPage, HTMLPage):
    def is_here(self):
        return self.doc.xpath('//h3[contains(text(), "PARTS DE SCPI")]')

    def go_scpi_detail_page(self):
        is_on_detail_page = self.doc.xpath('//a[contains(text(), "Quantité")]')
        if not is_on_detail_page:
            invest_element = self.doc.xpath('//table//a')
            assert len(invest_element) == 1
            self.browser.location('https://www.hsbc.fr' + CleanText('./@href')(invest_element[0]))

    def go_more_scpi_detail_page(self):
        detail_page = self.doc.xpath('//a[contains(@id, "productDetailForm") and contains(text(), "Consultez le détail")]')
        if detail_page:
            assert len(detail_page) == 1
            self.browser.location('https://www.hsbc.fr' + CleanText('./@href')(detail_page[0]))

    def go_scpi_his_detail_page(self):
        return Link('//div/a[contains(text(), "Historique de vos mouvements de parts")]')(self.doc)

    @method
    class iter_scpi_investment(TableElement):
        item_xpath = '//table[@class="csTable"]//tbody//tr'
        head_xpath = '//table[@class="csTable"]//thead//th/a'

        col_label = 'Nature'
        col_quantity = 'Quantité'
        col_unitprice = 'Prix de revient (en €)'
        col_unitvalue = ["Prix de retrait (en €)", "Valeur d'expertise (en €) *"]
        col_diff_percent = '(+/-) value en %'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_quantity = CleanDecimal(TableCell('quantity'))
            obj_unitprice = CleanDecimal(TableCell('unitprice'), replace_dots=True)
            obj_unitvalue = CleanDecimal(TableCell('unitvalue'), replace_dots=True)

            def obj_diff_ratio(self):
                diff_percent = CleanDecimal(
                    Regexp(CleanText(TableCell('diff_percent')), r'\d+,\d+'),
                    replace_dots=True
                )(self)
                return diff_percent / 100
