# -*- coding: utf-8 -*-

# Copyright(C) 2016      Edouard Lambert
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
from io import BytesIO
from urllib.parse import urljoin

import requests

from woob.browser.pages import (
    HTMLPage, RawPage, LoggedPage, pagination,
    FormNotFound, PartialHTMLPage, JsonPage,
)
from woob.browser.elements import ItemElement, TableElement, SkipItem, method
from woob.browser.filters.standard import (
    CleanText, Date, Regexp, Eval, CleanDecimal,
    Env, Field, MapIn, Upper, Format, Title, QueryValue,
    BrowserURL, Coalesce, Base,
)
from woob.browser.filters.html import (
    Attr, TableCell, AbsoluteLink, XPath,
    Link, HasElement,
)
from woob.browser.filters.json import Dict
from woob.browser.filters.javascript import JSVar
from woob.browser.exceptions import HTTPNotFound, LoggedOut
from woob.capabilities.bank import (
    Account, Transaction, AccountOwnerType,
)
from woob.capabilities.bank.wealth import Investment, Pocket
from woob.capabilities.profile import Person
from woob.capabilities.bill import Document, DocumentTypes
from woob.capabilities.base import NotAvailable, empty
from woob.tools.captcha.virtkeyboard import MappedVirtKeyboard
from woob.exceptions import (
    BrowserUnavailable, ActionNeeded, ActionType, BrowserIncorrectPassword,
)
from woob.tools.capabilities.bank.investments import (
    is_isin_valid, IsinCode, IsinType,
)
from woob.tools.json import json


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True, default=NotAvailable)
    return CleanDecimal(*args, **kwargs)


def percent_to_ratio(value):
    if empty(value):
        return NotAvailable
    return value / 100


class ErrorPage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable()


class S2eVirtKeyboard(MappedVirtKeyboard):
    symbols = {
        '0': (
            '8adee734aaefb163fb008d26bb9b3a42',
            '922d79345bf824b1186d0aa523b37a7c',
            '914fe440741b5d905c62eb4fa89efff2',
        ),
        '1': (
            'b815d6ce999910d48619b5912b81ddf1',
            '4730473dcd86f205dff51c59c97cf8c0',
            'dc1990415f4099d77743b0a1e3da0e84',
        ),
        '2': (
            '54255a70694787a4e1bd7dd473b50228',
            '2d8b1ab0b5ce0b88abbc0170d2e85b7e',
            'bbce0f83063bb2c58b041262c598a2c2',
        ),
        '3': (
            'ba06373d2bfba937d00bf52a31d475eb',
            '08e7e7ab7b330f3cfcb819b95eba64c6',
            'ab61fd800d2f1043f36b0b5c786d28f4',
        ),
        '4': (
            '3fa795ac70247922048c514115487b10',
            'ffb3d035a3a335cfe32c59d8ee1302ad',
            'ec4a4f06482410cf6cc6fdb488e527de',
        ),
        '5': (
            '788963d15fa05832ee7640f7c2a21bc3',
            'c4b12545020cf87223901b6b35b9a9e2',
            'd32ddd212be9a6e2d80b1330722b1ef2',
        ),
        '6': (
            'c8bf62dfaed9feeb86934d8617182503',
            '473357666949855a0794f68f3fc40127',
            '1437471444d09c19217518b602eb76a0',
        ),
        '7': (
            'f7543fdda3039bdd383531954dd4fc46',
            '5f3a71bd2f696b8dc835dfeb7f32f92a',
            '4a9714321387fdd08ae893d16c75138f',
        ),
        '8': (
            '5c4210e2d8e39f7667d7a9e5534b18b7',
            'b9a1a73430f724541108ed5dd862431b',
            '86c54698f26de51f10891a02b5315290',
        ),
        '9': (
            '94520ac801883fbfb700f43cd4172d41',
            '12c18ca3d4350acd077f557ac74161e5',
            'fb555d29e5eab741cdf16ed5c50d9428',
        ),
    }

    color = (0, 0, 0)

    def __init__(self, page, vkid):
        img = page.doc.find('//img[@id="clavier_virtuel"]')
        res = page.browser.open("/portal/rest/clavier_virtuel/%s" % vkid)
        MappedVirtKeyboard.__init__(self, BytesIO(res.content), page.doc, img, self.color, convert='RGB')
        self.check_symbols(self.symbols, None)

    def get_symbol_code(self, md5sum):
        code = MappedVirtKeyboard.get_symbol_code(self, md5sum)
        m = re.search(r'(\d+)', code)
        if m:
            return m.group(1)

    def get_string_code(self, string):
        return ''.join([self.get_symbol_code(self.symbols[c]) for c in string])


class BrowserIncorrectAuthenticationCode(BrowserIncorrectPassword):
    pass


class LoginErrorPage(PartialHTMLPage):
    pass


class TemporarilyUnavailablePage(HTMLPage):
    """
    The server isn't responding well because of huge activity.
    We can just retry and it will work fine.

    message: `Due to a peak of activity, our site is temporarily unavailable. We invite you to log in later.`
    """
    def get_unavailability_message(self):
        return CleanText('''//p[contains(text(), "pic d'activité")]''')(self.doc)


class SetCookiePage(HTMLPage):
    """
    Sometimes `browser.login.go()` redirects us here.
    It only returns a 404. We catch it and retry the login again.
    """
    pass


class LoginPage(HTMLPage):
    def get_password(self, password, secret):
        vkid = Attr('//input[@id="identifiantClavierVirtuel"]', 'value')(self.doc)
        code = S2eVirtKeyboard(self, vkid).get_string_code(password)
        tcc = Attr('//input[@id="codeTCC"]', 'value')(self.doc)
        password = "%s|%s|#%s#" % (code, vkid, tcc)
        if secret:
            password = "%s%s" % (password, secret)
        return password

    def login(self, login, password, secret):
        form = self.get_form(id="formulaireEnvoi")
        device_print = '''{"screen":{"screenWidth":500,"screenHeight":500,"screenColourDepth":24},"timezone":{"timezone":-60},"plugins":{"installedPlugins":""},"fonts":{"installedFonts":"cursive;monospace;serif;sans-serif;fantasy;default;Arial;Courier;Courier New;Gentium;Times;Times New Roman;"},"userAgent":"Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.78 Mobile Safari/537.36","appName":"Netscape","appCodeName":"Mozilla","appVersion":"5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.78 Mobile Safari/537.36","platform":"Linux x86_64","product":"Gecko","productSub":"20030107","vendor":"Google Inc.","language":"en-US"}'''
        form['password'] = self.get_password(password, secret) + device_print
        form['username'] = login
        form['devicePrint'] = device_print
        form.submit()

    def get_error(self):
        cgu = CleanText('//h1[contains(text(), "Conditions")]', default=None)(self.doc)
        if cgu:
            if self.browser.LANG == "fr":
                cgu = "Veuillez accepter les conditions générales d'utilisation."
            elif self.browser.LANG == 'en':
                cgu = "Please accept the general conditions of use."

        return cgu or CleanText('//div[contains(text(), "Erreur")]', default='')(self.doc)

    def send_otp(self, otp):
        try:
            form = self.get_form(
                xpath='//form[.//div[has-class("authentification-bloc-content-btn-bloc")]]',
                submit='//div[has-class("authentification-bloc-content-btn-bloc")]//input[@type="submit"]'
            )
        except FormNotFound:
            form = self.get_form(xpath='//form[.//div[contains(@class, "otp")]]')
            input_validate = (
                Attr('//a[.//span[contains(text(), "VALIDATE")]]', 'onclick', default=None)(self.doc)
                or Attr('//a[.//span[contains(text(), "VALIDER")]]', 'onclick', default=None)(self.doc)
                or Attr('//a[.//span[contains(text(), "Confirm")]]', 'onclick')(self.doc)
            )
            m = re.search(r"{\\'([^\\]+)\\':\\'([^\\]+)\\'}", input_validate)
            form[m.group(1)] = m.group(2)
            form.pop('pb12876:j_idt3:j_idt158:j_idt159:j_idt244:j_idt273', None)

        for k in form:
            if 'need help' in form[k].lower():
                del form[k]
                break

        input_otp = Attr('//input[contains(@id, "otp")]', 'id')(self.doc)
        input_id = Attr('//input[@type="checkbox"]', 'id')(self.doc)
        form[input_otp] = otp
        form[input_id] = 'on'
        form.submit()

    def check_error(self):
        if (
            bool(self.doc.xpath('//span[@class="operation-bloc-content-message-erreur-text"][contains(text(), "est incorrect")]'))
            or bool(self.doc.xpath('//span[@class="operation-bloc-content-message-erreur-text"][contains(text(), "is incorrect")]'))
        ):
            raise BrowserIncorrectAuthenticationCode('Invalid OTP')

        for errmsg_xpath in [
            '//span[@class="operation-bloc-content-message-erreur-text"][contains(text(), "Technical error")]',
            '//div[has-class("PORTLET-FRAGMENT")][contains(text(), "This portlet encountered an error and could not be displayed")]',
        ]:
            msg = CleanText(errmsg_xpath)(self.doc)
            if msg:
                raise BrowserUnavailable(msg)

    def is_login_form_available(self):
        return (
            HasElement('//form[@id="formulaireEnvoi"]')(self.doc)
            and HasElement('//input[@id="identifiantClavierVirtuel"]')(self.doc)
        )

    def is_otp_form_available(self):
        return HasElement('//form[contains(@id, "formSaisieOtp")]')(self.doc)

    def get_form_send_otp(self):
        """ Look for the form to send an OTP """
        receive_code_btn = bool(self.doc.xpath('//div[has-class("authentification-bloc-content-btn-bloc")][count(input)=1]'))
        submit_input = self.doc.xpath('//input[@type="submit"]')
        if receive_code_btn and len(submit_input) == 1:
            form = self.get_form(
                xpath='//form[.//div[has-class("authentification-bloc-content-btn-bloc")][count(input)=1]]',
                submit='//div[has-class("authentification-bloc-content-btn-bloc")]//input[@type="submit"]'
            )
            return form


class LandingPage(LoggedPage, HTMLPage):
    pass


class HsbcVideoPage(LoggedPage, HTMLPage):
    pass


class HsbcTokenPage(LoggedPage, HTMLPage):
    pass


class HsbcInvestmentPage(LoggedPage, HTMLPage):
    def get_params(self):
        raw_params = Regexp(CleanText('//script'), r'window.HSBC.dpas = ({.*?});')(self.doc)
        # We need to remove trailing commas from the JS object.
        # The replace is not super strict but it's good enough, the strings don't contain curly brackets
        raw_params = raw_params.replace(', }', '}')
        # dict has unquoted or badly quoted keys, that is accepted by js but not a valid json.
        # so, re add quotes when missing
        raw_params = re.sub(r"([\{\s,])'?(\w+)'?(:)", r'\1"\2"\3', raw_params)
        raw_params = re.sub(r"([\{\s,])'([^'\s]+)'([\}\s,])", r'\1"\2"\3', raw_params)

        return json.loads(raw_params)


class CodePage(object):
    '''
    This class is used as a parent class to include
    all classes that contain a get_code() method.
    '''
    def get_asset_category(self):
        # Overriden for pages containing the asset category.
        return NotAvailable


# AMF codes
class AMFHSBCPage(LoggedPage, JsonPage, CodePage):
    ENCODING = "UTF-8"
    CODE_TYPE = Investment.CODE_TYPE_AMF

    def get_code(self):
        for entry in self.doc['items']:
            title = entry.get('title')
            if title == 'Code AMF':
                return entry.get('value', NotAvailable)
        return NotAvailable

    def get_code_from_search_result(self, share_class):
        for fund in self.doc['funds']:
            for class_ in fund['shareClasses']:
                if class_['name'] == share_class:
                    # It's named ISIN but it's AMF
                    return class_['isin']
        return NotAvailable


class CmCicInvestmentPage(LoggedPage, HTMLPage):
    def get_ddp(self):
        # This value is required to access the page containing the investment data.
        # For some reason they added 'ddp=' at the beginning of the ddp itself...
        ddp = JSVar(CleanText('//script'), var='ddp')(self.doc)
        return ddp.replace('ddp=', '')

    def get_code(self):
        return CleanText(
            '//th[span[contains(text(), "Code valeur")]]/following-sibling::td//span',
            default=NotAvailable
        )(self.doc)

    def get_performance_history(self):
        durations = [CleanText('.')(el) for el in self.doc.xpath('//table[@id="t_PerformancesEnDate"]//thead//span')]
        values = [CleanText('.')(el) for el in self.doc.xpath('//table[@id="t_PerformancesEnDate"]//tbody//tr[1]//td')]
        matches = dict(zip(durations, values))
        perfs = {}
        for k, v in {1: '1 an', 3: '3 ans', 5: '5 ans'}.items():
            if matches.get(v):
                perfs[k] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches[v]))
        return perfs


class AmundiPage(LoggedPage, HTMLPage, CodePage):
    CODE_TYPE = Investment.CODE_TYPE_ISIN

    def get_code(self):
        return Regexp(
            CleanText('//div[@class="amundi-fund-legend"]', default=NotAvailable),
            r'ISIN: (\w+)',
            default=NotAvailable
        )(self.doc)

    def get_tab_url(self, tab_id):
        return Format(
            '%s%d',
            Regexp(CleanText('//script[contains(text(), "Product.init")]'), r'init\(.*?,"(.*?tab_)\d"', default=None),
            tab_id
        )(self.doc)

    def get_details_url(self):
        return self.get_tab_url(5)

    def get_performance_url(self):
        return self.get_tab_url(2)


class AMFSGPage(LoggedPage, HTMLPage, CodePage):
    CODE_TYPE = Investment.CODE_TYPE_AMF

    def build_doc(self, data):
        if not data.strip():
            # sometimes the page is totally blank... prevent an XMLSyntaxError
            data = b'<html></html>'
        return super(AMFSGPage, self).build_doc(data)

    def get_code(self):
        return Regexp(CleanText('//div[@id="header_code"]'), r'(\d+)', default=NotAvailable)(self.doc)

    def get_investment_performances(self):
        # TODO: Handle supplementary attributes for AMFSGPage
        self.logger.warning('This investment leads to AMFSGPage, please handle SRRI, asset_category and recommended_period.')

        # Fetching the performance history (1 year, 3 years & 5 years)
        perfs = {}
        if not self.doc.xpath('//table[tr[th[contains(text(), "Performances glissantes")]]]'):
            return
        # Available performance durations are: 1 week, 1 month, 1 year, 3 years & 5 years.
        # We need to match the durations with their respective values.
        durations = [CleanText('.')(el) for el in self.doc.xpath('//table[tr[th[contains(text(), "Performances glissantes")]]]//tr[2]//th')]
        values = [CleanText('.')(el) for el in self.doc.xpath('//table[tr[th[contains(text(), "Performances glissantes")]]]//td')]
        matches = dict(zip(durations, values))
        perfs[1] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['1 an *']))
        perfs[3] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['3 ans *']))
        perfs[5] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['5 ans *']))
        return perfs


class LyxorfcpePage(LoggedPage, HTMLPage, CodePage):
    CODE_TYPE = Investment.CODE_TYPE_ISIN

    def get_code(self):
        return Regexp(CleanText('//span[@class="isin"]'), 'Code ISIN : (.*)')(self.doc)


class LyxorFundsPage(LoggedPage, HTMLPage):
    @method
    class fill_investment(ItemElement):
        obj_asset_category = CleanText('//div[contains(@class, "asset-class-picto")]//h4')

        def obj_performance_history(self):
            # Fetching the performance history (1 year, 3 years & 5 years)
            perfs = {}
            if not self.xpath('//table[tr[td[text()="Performance"]]]'):
                return
            # Available performance history: 1 month, 3 months, 6 months, 1 year, 2 years, 3 years, 4 years & 5 years.
            # We need to match the durations with their respective values.
            durations = [CleanText('.')(el) for el in self.xpath('//table[tr[td[text()="Performance"]]]//tr//th')]
            values = [CleanText('.')(el) for el in self.xpath('//table[tr[td[text()="Performance"]]]//tr//td')]
            matches = dict(zip(durations, values))
            perfs[1] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['1A']))
            perfs[3] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['3A']))
            perfs[5] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches['5A']))
            return perfs


class EcofiPage(LoggedPage, HTMLPage, CodePage):
    CODE_TYPE = Investment.CODE_TYPE_ISIN

    def get_code(self):
        return CleanText('//div[has-class("field-name-CodeISIN")]/div[@class="field-items"]')(self.doc)


class EcofiDummyPage(LoggedPage, RawPage):
    pass


class ItemInvestment(ItemElement):
    klass = Investment

    obj_unitvalue = Env('unitvalue')
    obj_vdate = Env('vdate')
    obj_code = Env('code')
    obj_code_type = Env('code_type')
    obj__link = Env('_link')
    obj_asset_category = Env('asset_category')
    obj_label = Env('label')

    def obj_valuation(self):
        return MyDecimal(TableCell('valuation')(self)[0].xpath('.//div[not(.//div)]'))(self)

    def obj_srri(self):
        # We search "isque" because it can be "Risque" or "Echelle de risque"
        srri = CleanText(
            TableCell('label')(self)[0].xpath('.//div[contains(text(), "isque")]//span[1]'),
        )(self)
        if srri:
            return int(srri)
        return NotAvailable

    def obj_recommended_period(self):
        return CleanText(
            TableCell('label')(self)[0].xpath('.//div[contains(text(), "isque")]//span[2]'),
        )(self)

    def parse(self, el):
        label_block = TableCell('label')(self)[0]
        label = CleanText(
            label_block.xpath('.//div[contains(@style, "text-align")][1]')
        )(self)
        self.env['label'] = label

        # Trying to find vdate and unitvalue
        unitvalue, vdate = None, None
        for span in label_block.xpath('.//span'):
            if unitvalue is None:
                # there is a space if unitvalue >= 1k, so regex can match "1 234,567" or "123,456" or "123".
                unitvalue = Regexp(CleanText('.'), r'(^(\d{1,3}\s)*\d{1,3}(,\d*)?)$', default=None)(span)
            if vdate is None:
                raw_label = CleanText('./parent::div')(span)
                if not any(x in raw_label for x in ["échéance", "Maturity"]):
                    vdate = Regexp(CleanText('.'), r'^([\d\/]+)$', default=None)(span)
        if unitvalue:
            self.env['unitvalue'] = MyDecimal().filter(unitvalue)
        else:
            self.env['unitvalue'] = NotAvailable
        if vdate:
            self.env['vdate'] = Date(dayfirst=True).filter(vdate)
        else:
            self.env['vdate'] = NotAvailable

        self.env['_link'] = None
        self.env['asset_category'] = NotAvailable

        page = None
        link_id = Attr(u'.//a[contains(@title, "détail du fonds")]', 'id', default=None)(self)
        inv_id = Attr('.//a[contains(@id, "linkpdf")]', 'id', default=None)(self)

        if link_id and inv_id:
            form = self.page.get_form('//div[@id="operation"]//form')
            form['idFonds'] = inv_id.split('-', 1)[-1]
            form['org.richfaces.ajax.component'] = form[link_id] = link_id
            page = self.page.browser.open(form['javax.faces.encodedURL'], data=dict(form)).page

            if 'hsbc.fr' in self.page.browser.BASEURL:
                # Special space for HSBC, does not contain any information related to performances.
                url = Regexp(
                    CleanText('//complete', default=''),
                    r"openUrlFichesFonds\('([^']+)'",
                    default=''
                )(page.doc)

                m = re.search(r'fundid=(\w+).+SH=(\w+)', url)
                # had to put full url to skip redirections.
                if m:
                    fund_id = m.group(1)
                    share_class = m.group(2)
                    if "/fcpe-closed" in url:
                        # This are non public funds, so they are not visible on search engine.
                        page = page.browser.open(BrowserURL('hsbc_investments', fund_id=fund_id)(self)).page
                        hsbc_params = page.get_params()
                        share_id = hsbc_params['pageInformation']['shareId']
                        self.env['code'] = share_id
                        self.env['code_type'] = page.CODE_TYPE
                        self.env['asset_category'] = NotAvailable
                        return
                    else:
                        page = page.browser.open(BrowserURL('hsbc_investments')(self)).page
                        hsbc_params = page.get_params()
                        hsbc_token_id = hsbc_params['pageInformation']['dataUrl']['id']
                        page = page.browser.open(
                            BrowserURL('hsbc_token_page')(self),
                            headers={
                                'X-Component': hsbc_token_id,
                                'X-Country': 'FR',
                                'X-Language': 'FR',
                            },
                            method='POST',
                        ).page

                        hsbc_token = page.text
                        hsbc_params['paging'] = {'currentPage': 1}
                        hsbc_params['searchTerm'] = [fund_id]
                        hsbc_params['view'] = 'Prices'
                        hsbc_params['appliedFilters'] = []
                        page = page.browser.open(
                            BrowserURL('amfcode_search_hsbc')(self),
                            headers={'Authorization': 'Bearer %s' % hsbc_token},
                            json=hsbc_params,
                        ).page
                        self.env['code'] = page.get_code_from_search_result(share_class)
                        self.env['code_type'] = page.CODE_TYPE
                        self.env['asset_category'] = NotAvailable
                        return
                elif '/videos-pedagogiques/' in url:
                    # For some invests (ex.: fonds-hsbc-ee-dynamique),
                    # the URL doesn't go directly to the correct page.
                    # It goes on another page which URLs to related funds.
                    page = page.browser.open(url).page
                    fund_links = page.doc.xpath('//a[@class="inline-link--internal"]')
                    for a_block in fund_links:
                        share_class = Regexp(
                            Attr('.', 'title'),
                            r'- part (\w+) \(',
                            default=NotAvailable
                        )(a_block)
                        code = Regexp(
                            Link('.'),
                            r'/fr/epargnants/fund-centre/(\w+)(?:\?.*)',
                            default=NotAvailable
                        )(a_block)

                        if share_class and ' (%s) - ' % share_class in label:
                            self.env['code'] = code
                            self.env['code_type'] = Investment.CODE_TYPE_AMF
                            self.env['asset_category'] = NotAvailable
                            return

            elif not self.page.browser.history.is_here():
                url = page.get_invest_url()

                if empty(url):
                    self.env['code'] = NotAvailable
                    self.env['code_type'] = NotAvailable
                    return

                # URLs used in browser.py to access investments performance history:
                if url.startswith('https://optimisermon.epargne-retraite-entreprises'):
                    # This URL can be used to access the BNP Wealth API to fetch investment performance and ISIN code
                    self.env['_link'] = url
                    self.env['code'] = NotAvailable
                    self.env['code_type'] = NotAvailable
                    return
                elif (
                    url.startswith('http://sggestion-ede.com/product')
                    or url.startswith('https://www.lyxorfunds.com/part')
                    or url.startswith('https://www.societegeneralegestion.fr')
                    or url.startswith('https://www.amundi-ee.com')
                    or url.startswith('http://www.etoile-gestion.com/productsheet')
                    or url.startswith('https://www.cpr-am.fr')
                    or url.startswith('https://www.cmcic-am.fr/fr/particuliers/nos-fonds/VALE_Fiche')
                    or url.startswith('https://www.assetmanagement.hsbc.com/fr/fcpe-closed')
                ):
                    self.env['_link'] = url

                # Try to fetch ISIN code from URL with re.match
                match = re.match(r'https://www.cpr-am.fr/fr/fonds_detail.php\?isin=([A-Z0-9]+)', url)
                match = match or re.match(r'https://www.cpr-am.fr/particuliers/product/view/([A-Z0-9]+)', url)
                if match:
                    self.env['code'] = match.group(1)
                    if is_isin_valid(match.group(1)):
                        self.env['code_type'] = Investment.CODE_TYPE_ISIN
                    else:
                        self.env['code_type'] = Investment.CODE_TYPE_AMF
                    return

                # Try to fetch ISIN code from URL with re.search
                m = re.search(r'&ISIN=([^&]+)', url)
                m = m or re.search(r'&isin=([^&]+)', url)
                m = m or re.search(r'&codeIsin=([^&]+)', url)
                m = m or re.search(r'lyxorfunds\.com/part/([^/]+)', url)
                if m:
                    self.env['code'] = m.group(1)
                    if is_isin_valid(m.group(1)):
                        self.env['code_type'] = Investment.CODE_TYPE_ISIN
                    else:
                        self.env['code_type'] = Investment.CODE_TYPE_AMF
                    return

                useless_urls = (
                    # pdf... http://docfinder.is.bnpparibas-ip.com/api/files/040d05b3-1776-4991-aa49-f0cd8717dab8/1536
                    'http://docfinder.is.bnpparibas-ip.com/',
                    # The AXA website displays performance graphs but everything is calculated using JS scripts.
                    # There is an API but it only contains risk data and performances per year, not 1-3-5 years.
                    'https://epargne-salariale.axa-im.fr/fr/',
                    # This URL leads to a connection error even on the website
                    'https://epargneentreprise.axa.fr',
                    # Redirection to the Rothschild Gestion website, which doesn't exist anymore...
                    'https://www.rothschildgestion.com',
                    # URL to the Morningstar website does not contain any useful information
                    'http://doc.morningstar.com',
                    # URL to Russell investments directly leads to the DICI PDF
                    'https://russellinvestments.com',
                    # This URL is automatically opened and leads us to an error page.
                    # The Comgest website doesn't contain any useful information.
                    'https://www.comgest.com',
                )
                for useless_url in useless_urls:
                    if url.startswith(useless_url):
                        self.env['code'] = NotAvailable
                        self.env['code_type'] = NotAvailable
                        return

                if url.startswith('http://fr.swisslife-am.com/fr/'):
                    self.page.browser.session.cookies.set('location', 'fr')
                    self.page.browser.session.cookies.set('prof', 'undefined')
                try:
                    page = self.page.browser.open(url).page
                except HTTPNotFound:
                    # Some pages lead to a 404 so we must avoid unnecessary crash
                    self.logger.warning('URL %s was not found, investment details will be skipped.', url)

        if isinstance(page, CodePage):
            self.env['code'] = page.get_code()
            self.env['code_type'] = page.CODE_TYPE
            self.env['asset_category'] = page.get_asset_category()
        else:
            # The page is not handled and does not have a get_code method.
            self.env['code'] = NotAvailable
            self.env['code_type'] = NotAvailable
            self.env['asset_category'] = NotAvailable


class MultiPage(HTMLPage):
    def on_load(self):
        self.check_disconnected()

    def get_multi(self):
        return [
            Attr('.', 'value')(option) for option in self.doc.xpath('//select[@class="ComboEntreprise"]/option')
        ]

    def go_multi(self, id):
        if Attr('//select[@class="ComboEntreprise"]/option[@selected]', 'value')(self.doc) != id:
            form = self.get_form('//select[@class="ComboEntreprise"]/ancestor::form[1]')
            key = [k for k, v in dict(form).items() if "SelectItems" in k][0]
            form[key] = id
            form['javax.faces.source'] = key
            form.submit()

    def check_disconnected(self):
        """Check disconnection.

        When we are disconnected, a page is returned with meta refresh redirection
        as content.
        A possible root reason to test is if the user is connect to its account at the same time.
        """
        if self.doc.xpath('//meta[@http-equiv="refresh"]/@content'):
            raise LoggedOut()


class AccountsInfoPage(LoggedPage, MultiPage):
    def get_account_info(self):
        accounts_info = dict()
        # we get all the IDs & labels for every account on the user space
        accs = self.doc.xpath('//div[contains(@class, "NomCodeDispositif")]//div')
        for account in accs:
            id, label = CleanText(account)(self.doc).split(' ', 1)
            if label in accounts_info:
                accounts_info[label].append(id)
            else:
                accounts_info[label] = [id]
        return accounts_info


ACCOUNT_TYPES = {
    'PEE': Account.TYPE_PEE,
    'PEI': Account.TYPE_PEE,
    'PEEG': Account.TYPE_PEE,
    'PEG': Account.TYPE_PEE,
    'PLAN': Account.TYPE_PEE,
    'PAGA': Account.TYPE_PEE,
    'ABONDEMENT EXCEPTIONNEL': Account.TYPE_PEE,
    'REINVESTISSEMENT DIVIDENDES': Account.TYPE_PEE,
    'PERCO': Account.TYPE_PERCO,
    'PERCOI': Account.TYPE_PERCO,
    'PERECO': Account.TYPE_PER,
    'SWISS': Account.TYPE_MARKET,
    'RSP': Account.TYPE_RSP,
    'CCB': Account.TYPE_RSP,
    'PARTICIPATION': Account.TYPE_DEPOSIT,
    'PERF': Account.TYPE_PERP,
}


class AccountsPage(LoggedPage, MultiPage):
    def on_load(self):
        super(AccountsPage, self).on_load()
        if CleanText(
            '//a//span[contains(text(), "CONDITIONS GENERALES") or contains(text(), "GENERAL CONDITIONS")]'
        )(self.doc):
            raise ActionNeeded(
                locale="fr-FR", message="Veuillez valider les conditions générales d'utilisation",
                action_type=ActionType.ACKNOWLEDGE,
            )

    CONDITIONS = {
        u'disponible': Pocket.CONDITION_AVAILABLE,
        u'épargne': Pocket.CONDITION_AVAILABLE,
        u'available': Pocket.CONDITION_AVAILABLE,
        u'withdrawal': Pocket.CONDITION_RETIREMENT,
        u'retraite': Pocket.CONDITION_RETIREMENT,
    }

    def get_no_accounts_message(self):
        no_accounts_message = CleanText(
            '''//span[contains(text(), "A ce jour, vous ne disposez plus d\'épargne salariale dans cette entreprise.")] |
            //span[contains(text(), "A ce jour, vous ne disposez pas encore d\'épargne salariale dans cette entreprise.")] |
            //span[contains(text(), "Vous ne disposez plus d'épargne salariale.")] |
            //span[contains(text(), "Vous ne disposez pas d'épargne salariale.")] |
            //span[contains(text(), "On this date, you still have no employee savings in this company.")] |
            //span[contains(text(), "On this date, you do not yet have any employee savings in this company.")] |
            //span[contains(text(), "On this date, you no longer have any employee savings in this company.")] |
            //p[contains(text(), "You do not have any employee savings.")] |
            //p[contains(text(), "You no longer have any employee savings.")]'''
        )(self.doc)
        return no_accounts_message

    def get_error_message(self):
        return CleanText('//div[@id="operation"]//div[@class="PORTLET-FRAGMENT"][contains(text(), "error")]')(self.doc)

    @method
    class iter_accounts(TableElement):
        item_xpath = '//div[contains(@id, "Dispositif")]//table/tbody/tr'
        head_xpath = '//div[contains(@id, "Dispositif")]//table/thead/tr/th'

        col_label = [u'My schemes', u'Mes dispositifs']
        col_balance = [re.compile(u'Total'), re.compile(u'Montant')]

        class item(ItemElement):
            klass = Account

            # the account has to have a color correspondig to the graph
            # if not, it may be a duplicate
            def condition(self):
                return (
                    self.xpath('.//div[contains(@class, "mesavoirs-carre-couleur") and contains(@style, "background-color:#")]')
                )

            # We can't determine the id, yet, as it comes from another page and there
            # can be multiple accounts with the same label.
            obj_id = None
            # HTML Table on the website is bad so i use my own xpath without TableCell
            obj_type = MapIn(Upper(Field('label')), ACCOUNT_TYPES, Account.TYPE_PEE)
            obj_owner_type = AccountOwnerType.PRIVATE

            def obj_label(self):
                return Coalesce(
                    CleanText('.//td[1]//a'),
                    CleanText('.//td[1]/text()'),
                    default=NotAvailable
                )(self)

            def obj_balance(self):
                return MyDecimal(TableCell('balance')(self)[0].xpath('.//div[has-class("nowrap")]'))(self)

            def obj_currency(self):
                return Account.get_currency(
                    CleanText(TableCell('balance')(self)[0].xpath('.//div[has-class("nowrap")]'))(self)
                )

    @method
    class fill_account(ItemElement):
        def obj_id(self):
            account_info = Env('account_info')(self)
            seen_account_ids = Env('seen_account_ids')(self)
            ids = account_info.get(Env('label')(self))
            if ids:
                possible_ids = [_id for _id in ids if _id not in seen_account_ids]
                if possible_ids:
                    return possible_ids[0]

        obj_number = Field('id')
        obj_company_name = Env('company_name')
        obj__space = Env('space')

    def drop_key_in_form(self, form, partial_key):
        for key in dict(form).keys():
            if partial_key in key:
                del form[key]

    def has_form(self):
        return HasElement('//div[@id="operation"]//form')(self.doc)

    def change_tab(self, tab):
        form = self.get_form(xpath='//div[@id="operation"]//form')
        input_id = Attr('//input[contains(@id, "onglets")]', 'name')(self.doc)
        spaces_to_tab = {
            'account': 'onglet1',
            'investment': 'onglet2',
            'pocket': 'onglet4',
        }

        # Prevent redirection to the stock options page
        self.drop_key_in_form(form, 'RedirectionBlocages')

        form[input_id] = spaces_to_tab[tab]
        form.submit()

    def get_investment_pages(self, accid, valuation=True, pocket=False):
        form = self.get_form(xpath='//div[@id="operation"]//form')
        input_id = Attr('//input[contains(@id, "onglets")]', 'name')(self.doc)

        if pocket:
            form[input_id] = "onglet4"
            form['visualisationMontant'] = str(bool(valuation)).lower()
            onglet_id_name = ":detailParSupportEtDate"
            onglet_type_switch = ":linkChangerVisualisationParSupporEtDate"
        else:
            form[input_id] = "onglet2"
            form['valorisationMontant'] = str(bool(valuation)).lower()
            onglet_id_name = ":ongletDetailParSupport"
            onglet_type_switch = ":linkChangerTypeAffichageParSupport"

        select_id = Attr('//option[contains(text(), "%s")]/..' % accid, 'id')(self.doc)
        form[select_id] = Attr('//option[contains(text(), "%s")]' % accid, 'value')(self.doc)

        onglet_id_base = Attr('//div[ends-with(@id, "%s")]' % onglet_id_name, 'id')(self.doc)
        if onglet_id_base:
            # Remove the end of the id to get the base id of the div block
            onglet_id_base = onglet_id_base[:-len(onglet_id_name)]

        # In addition with the xxxMontant boolean input, another input should be
        # set to switch the "view" that is rendered for the content of the "onglet"
        # (ie by Valuation view or by Quantity view)
        # Ex: pb85155:j_idt2:form:j_idt3:j_idt387:linkChangerTypeAffichageParSupport2
        # ="pb85155:j_idt2:form:j_idt3:j_idt387:linkChangerTypeAffichageParSupport2"
        if valuation:
            type_index = '1'
        else:
            type_index = '2'
        input_onglet_type = '%s%s%s' % (onglet_id_base, onglet_type_switch, type_index)
        form[input_onglet_type] = input_onglet_type

        # Prevent redirection to the stock options page
        self.drop_key_in_form(form, 'RedirectionBlocages')

        form.submit()

    @method
    class iter_investment(TableElement):
        item_xpath = '//div[contains(@id, "ongletDetailParSupport")]//table/tbody/tr[td[4]]'
        head_xpath = '//div[contains(@id, "ongletDetailParSupport")]//table/thead/tr/th'

        col_label = [re.compile(u'My investment'), re.compile(u'Mes supports')]
        col_valuation = [re.compile(u'Gross amount'), re.compile(u'Montant brut')]
        col_portfolio_share = [u'Distribution', u'Répartition']
        col_diff = [u'+ or - potential value', u'+ ou - value potentielle']

        class item(ItemInvestment):
            def obj_diff(self):
                td = TableCell('diff', default=None)(self)
                if td:
                    return MyDecimal('.//div[not(.//div)]')(td[0])
                return NotAvailable

            def obj_portfolio_share(self):
                return Eval(
                    lambda x: x / 100,
                    MyDecimal(TableCell('portfolio_share')(self)[0].xpath('.//div[has-class("nowrap")]'))(self)
                )(self)

    def update_invs_quantity(self, invs):
        for inv in invs:
            inv.quantity = MyDecimal().filter(
                CleanText(
                    '//div[contains(@id, "ongletDetailParSupport")]//tr[.//div[contains(replace(text(), "\xa0", " "), "%s")]]/td[last()]//div/text()'
                    % inv.label
                )(self.doc)
            )
        return invs

    def get_invest_url(self):
        return Regexp(CleanText('//complete'), r"openUrlFichesFonds\('([^']+)", default=NotAvailable)(self.doc)

    @method
    class iter_pocket(TableElement):
        item_xpath = '//div[contains(@id, "detailParSupportEtDate")]//table/tbody[@class="rf-cst"]/tr[td[4]]'
        head_xpath = '//div[contains(@id, "detailParSupportEtDate")]//table/thead/tr/th'

        col_amount = [re.compile(u'Gross amount'), re.compile(u'Montant brut')]
        col_availability = [u'Availability date', u'Date de disponibilité']

        class item(ItemElement):
            klass = Pocket

            obj_availability_date = Env('availability_date')
            obj_condition = Env('condition')
            obj__matching_txt = Env('matching_txt')

            def obj_amount(self):
                return MyDecimal(TableCell('amount')(self)[0].xpath('.//div[has-class("nowrap")]'))(self)

            def obj_investment(self):
                investment = None
                for inv in self.page.browser.cache['invs'][Env('accid')(self)]:
                    if inv.label in CleanText('./parent::tbody/preceding-sibling::tbody[1]')(self):
                        investment = inv
                assert investment is not None
                return investment

            def obj_label(self):
                return Field('investment')(self).label

            def parse(self, el):
                txt = CleanText(TableCell('availability')(self)[0].xpath('./span'))(self)
                self.env['availability_date'] = Date(dayfirst=True, default=NotAvailable).filter(txt)
                if self.env['availability_date']:
                    self.env['condition'] = Pocket.CONDITION_DATE
                else:
                    self.env['condition'] = self.page.CONDITIONS.get(txt.lower().split()[0], Pocket.CONDITION_UNKNOWN)
                self.env['matching_txt'] = txt

    def update_pockets_quantity(self, pockets):
        for pocket in pockets:
            pocket.quantity = MyDecimal(CleanText(
                '''//div[contains(@id, "detailParSupportEtDate")]
                //tbody[.//div[contains(replace(text(), "\xa0", " "), "%s")]]/following-sibling::tbody[1]
                //tr[.//span[contains(text(), "%s")]]/td[last()]//div/text()'''
                % (pocket.investment.label, pocket._matching_txt)
            ))(self.doc)
        return pockets


class HistoryPage(LoggedPage, MultiPage):
    XPATH_FORM = '//div[@id="operation"]//form'

    def get_history_form(self, idt, args=None):
        form = self.get_form(self.XPATH_FORM)
        form[idt] = idt
        form['javax.faces.source'] = idt
        if not args:
            args = {}
        form.update(args)
        return form

    def show_more(self, nb):
        try:
            form = self.get_form(self.XPATH_FORM)
        except FormNotFound:
            return False
        for select in self.doc.xpath('//select'):
            if Attr('./option[@selected]', 'value')(select) == nb:
                return True
            idt = Attr('.', 'id')(select)
            form[idt] = nb
            if 'javax.faces.source' not in form:
                form['javax.faces.source'] = idt
        form.submit()
        return True

    def go_start(self):
        idt = Attr('//a[@title="debut" or @title="precedent"]', 'id', default=None)(self.doc)
        if idt:
            form = self.get_history_form(idt)
            form.submit()

    @method
    class get_investments(TableElement):
        item_xpath = '//table//table/tbody/tr[td[4]]'
        head_xpath = '//table//table/thead/tr/th'

        col_scheme = ['Scheme', 'Dispositif']
        col_label = [re.compile('Investment'), re.compile('My investment'), 'fund', re.compile('Support')]
        col_quantity = [re.compile('Quantity'), re.compile('Quantité'), re.compile('En parts'), re.compile('Nombre de parts')]
        col_valuation = ['Gross amount', 'Net amount', re.compile('.*Montant brut'), re.compile('.*Montant [Nn]et')]

        class item(ItemInvestment):
            def obj_quantity(self):
                return MyDecimal(TableCell('quantity')(self)[0].xpath('./text()'))(self)

            def condition(self):
                return Env('accid')(self) in CleanText(TableCell('scheme'))(self)

    @pagination
    @method
    class iter_history(TableElement):
        item_xpath = '//table/tbody/tr[td[4]]'
        head_xpath = '//table/thead/tr/th'

        col_id = [re.compile(u'Ref'), re.compile(u'Réf')]
        col_date = [re.compile(u'Date'), re.compile('Creation date')]
        col_label = [re.compile('Transaction'), re.compile(u'Type')]
        col_net_amount = ['Net amount', 'Montant net']
        col_net_employer_contribution_amount = [
            'Net employer contribution amount',
            'Montant net de l\'abondement',
            'Abondement net',
        ]

        def next_page(self):
            idt = Attr('//a[@title="suivant"]', 'id', default=None)(self.page.doc)
            if idt:
                form = self.page.get_history_form(idt)
                return requests.Request("POST", form.url, data=dict(form))

        class item(ItemElement):
            klass = Transaction

            obj_id = CleanText(TableCell('id'))
            obj_label = CleanText(TableCell('label'))
            obj_type = Transaction.TYPE_BANK
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)

            def obj_amount(self):
                net_amount = Base(
                    TableCell('net_amount'),
                    CleanDecimal.French('.//div', default=0),
                )(self)
                employer_contrib = Base(
                    TableCell('net_employer_contribution_amount'),
                    CleanDecimal.French('.//div', default=0)
                )(self)

                if net_amount or employer_contrib:
                    return net_amount + employer_contrib

                raise SkipItem()

            def parse(self, el):
                if Env('len_space_accs')(self) == 1:
                    # Single account in the space -> all transactions belong to it -> no need to visit details page
                    return
                self.match_account_transaction(el)

            def match_account_transaction(self, el):
                # For connections with multiple accounts, we need to go to the details to make sure
                # that the transaction is related to the account
                trid = CleanText(TableCell('id'))(self)
                if trid not in self.page.browser.cache['details']:
                    idt = Attr(TableCell('id')(self)[0].xpath('./a'), 'id', default=None)(self)
                    typeop = Regexp(
                        Attr(TableCell('id')(self)[0].xpath('./a'), 'onclick'),
                        r'Operation.+?([A-Z_]+)'
                    )(self)
                    form = self.page.get_history_form(idt, {'referenceOp': trid, 'typeOperation': typeop})
                    details_page = self.page.browser.open(form.url, data=dict(form)).page
                    details_page.check_disconnected()

                    # Cache
                    self.page.browser.cache['details'][trid] = details_page

                    # As the site is stateful, if we just previously requested a details page,
                    # then we have first to "go back" to the history list before trying to
                    # get the details of another transaction
                    idt = Attr('//input[@title="Retour"]', 'id', default=None)(details_page.doc)
                    if idt:
                        form = self.page.get_history_form(idt)
                        self.page.browser.open(form.url, data=dict(form))

                else:
                    # Load cache
                    details_page = self.page.browser.cache['details'][trid]
                # Skip transaction: not the right account
                if not len(details_page.doc.xpath('//td[contains(text(), $id)]', id=Env('accid')(self))):
                    raise SkipItem()


class StockOptionsPage(LoggedPage, HTMLPage):
    """
    Contains a table with the columns:
        - Origine du blocage (ex: Dividendes issus de LO)
        - Mes dispositifs (ex: 0000999999 PEE avoirs issus de SO)
        - Mes supports de placement (ex: 999 ACTIONNARIAT FRANCE)
        - Echéance (ex: 01/01/1970)
        - Nombre de parts (ex: 999,9999 p)
        - Fin du blocage (ex: 01/01/1970)
        - Opération bloquée (ex: Remboursement)
    """

    def on_load(self):
        self.logger.warning('Was redirected to StockOptionsPage. Stock options are not handled.')


class SwissLifePage(HTMLPage, CodePage):
    CODE_TYPE = Investment.CODE_TYPE_ISIN

    def get_code(self):
        code = CleanText(
            '//span[contains(text(), "Code ISIN")]/following-sibling::span[@class="data"]',
            default=NotAvailable
        )(self.doc)
        if code == "n/a":
            return NotAvailable
        return code


class EtoileGestionPage(HTMLPage, CodePage):
    CODE_TYPE = NotAvailable

    def get_code(self):
        # Codes (AMF / ISIN) are available after a click on a tab
        characteristics_url = urljoin(
            self.url,
            Attr(u'//a[contains(text(), "Caractéristiques")]', 'data-href', default=None)(self.doc)
        )
        if characteristics_url is not None:
            detail_page = self.browser.open(characteristics_url).page

            if not isinstance(detail_page, EtoileGestionCharacteristicsPage):
                return NotAvailable

            # We prefer to return an ISIN code by default
            code_isin = detail_page.get_isin_code()
            if code_isin is not None:
                self.CODE_TYPE = Investment.CODE_TYPE_ISIN
                return code_isin

            # But if it's unavailable we can fallback to an AMF code
            code_amf = detail_page.get_code_amf()
            if code_amf is not None:
                self.CODE_TYPE = Investment.CODE_TYPE_AMF
                return code_amf

        return NotAvailable

    def get_asset_category(self):
        return CleanText('//label[contains(text(), "Classe d\'actifs")]/following-sibling::span')(self.doc)


class EtoileGestionCharacteristicsPage(LoggedPage, PartialHTMLPage):
    def get_isin_code(self):
        code = CleanText('//td[contains(text(), "Code Isin")]/following-sibling::td', default=None)(self.doc)
        return code

    def get_code_amf(self):
        code = CleanText('//td[contains(text(), "Code AMF")]/following-sibling::td', default=None)(self.doc)
        return code

    def get_performance_history(self):
        perfs = {}
        if CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()-2]', default=None)(self.doc):
            perfs[1] = Eval(
                lambda x: x / 100,
                CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()-2]')
            )(self.doc)
        if CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()-1]', default=None)(self.doc):
            perfs[3] = Eval(
                lambda x: x / 100,
                CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()-1]')
            )(self.doc)
        if CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()]', default=None)(self.doc):
            perfs[5] = Eval(
                lambda x: x / 100,
                CleanDecimal.French('//tr[td[text()="Fonds"]]//td[position()=last()]')
            )(self.doc)
        return perfs


class EtoileGestionDetailsPage(LoggedPage, HTMLPage):
    def get_asset_category(self):
        return CleanText('//label[text()="Classe d\'actifs:"]/following-sibling::span')(self.doc)

    def get_performance_url(self):
        return Attr('(//li[@role="presentation"])[1]//a', 'data-href', default=None)(self.doc)


class EsaliaDetailsPage(LoggedPage, HTMLPage):
    def get_asset_category(self):
        return CleanText('//label[text()="Classe d\'actifs:"]/following-sibling::span')(self.doc)

    def get_performance_url(self):
        return Attr('//a[contains(text(), "Performances")]', 'data-href', default=None)(self.doc)


class EsaliaPerformancePage(LoggedPage, HTMLPage):
    def get_performance_history(self):
        # The positions of the columns depend on the age of the investment fund.
        # For example, if the fund is younger than 5 years, there will be not '5 ans' column.
        durations = [CleanText('.')(el) for el in self.doc.xpath('//div[contains(@class, "fpPerfglissanteclassique")]//th')]
        values = [CleanText('.')(el) for el in self.doc.xpath('//div[contains(@class, "fpPerfglissanteclassique")]//tr[td[text()="Fonds"]]//td')]
        matches = dict(zip(durations, values))
        # We do not fill the performance dictionary if no performance is available,
        # otherwise it will overwrite the data obtained from the JSON with empty values.
        perfs = {}
        for k, v in {1: '1 an', 3: '3 ans', 5: '5 ans'}.items():
            if matches.get(v):
                perfs[k] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches[v]))
        return perfs


class AmundiPerformancePage(EsaliaPerformancePage):
    '''
    The parsing of this page is exactly like EsaliaPerformancePage
    but the URL is quite different so we handle it with a separated page
    '''
    pass


class AmundiDetailsPage(LoggedPage, HTMLPage):
    def get_recommended_period(self):
        return Title(CleanText(
            '//label[contains(text(), "Durée minimum de placement")]/following-sibling::span',
            default=NotAvailable
        ))(self.doc)

    def get_asset_category(self):
        return CleanText(
            '(//label[contains(text(), "Classe d\'actifs")])[1]/following-sibling::span',
            default=NotAvailable
        )(self.doc)


class ProfilePage(LoggedPage, MultiPage):
    def get_company_name(self):
        return CleanText(
            '//div[contains(@class, "operation-bloc")]//span[contains(text(), "Entreprise :") or contains(text(), "Company :")]/following-sibling::span[1]'
        )(self.doc)

    @method
    class get_profile(ItemElement):
        klass = Person

        obj__civilite = CleanText('//div/span[contains(text(), "Civilité") or contains(text(), "Title")]/following-sibling::div/span')
        obj_lastname = CleanText('//div/span[contains(text(), "Nom") or contains(text(), "Name")]/following-sibling::div/span')
        obj_firstname = CleanText('//div/span[contains(text(), "Prénom") or contains(text(), "First name")]/following-sibling::div/span')
        obj_name = Format(u'%s %s %s', obj__civilite, obj_firstname, obj_lastname)
        obj_address = CleanText('//div/span[contains(text(), "Adresse postale") or contains(text(), "Postal address")]/following-sibling::div/div[2]')
        obj_phone = CleanText('//div/span[contains(text(), "Tél. portable") or contains(text(), "Mobile phone")]/following-sibling::div/span')
        obj_email = CleanText('//div/span[contains(text(), "E-mail")]/following-sibling::div/span')
        obj_company_name = CleanText('//div[contains(@class, "operation-bloc")]//span[contains(text(), "Entreprise :") or contains(text(), "Company :")]/following-sibling::span[1]')


class BNPInvestmentsPage(LoggedPage, HTMLPage):
    pass


class BNPInvestmentDetailsPage(LoggedPage, JsonPage):
    def is_content_valid(self):
        return not (self.text == 'null' or self.doc == [])

    @method
    class fill_investment(ItemElement):
        obj_code = IsinCode(CleanText(Dict('isin')), default=NotAvailable)
        obj_code_type = IsinType(CleanText(Dict('isin')))
        obj_asset_category = Dict('classification')
        obj_recommended_period = Dict('dureePlacement')

        def obj_srri(self):
            if Dict('risque')(self):
                return Eval(int, Dict('risque'))(self)
            return NotAvailable

        def obj_performance_history(self):
            if not Dict('sharePerf')(self):
                # No performance history available
                return NotAvailable

            perfs = {}
            # Fetching the performance history (1 year, 3 years & 5 years)
            for item in Dict('sharePerf')(self):
                if item['name'] in ('1Y', '3Y', '5Y'):
                    duration = int(item['name'][0])
                    value = item['value']
                    perfs[duration] = Eval(lambda x: x / 100, CleanDecimal.US(value))(self)
            return perfs


class CprInvestmentPage(LoggedPage, HTMLPage):
    @method
    class fill_investment(ItemElement):
        obj_srri = CleanText('//span[@class="active"]', default=NotAvailable)
        # Text headers can be in French or in English
        obj_asset_category = Title(
            '//div[contains(text(), "Classe d\'actifs") or contains(text(), "Asset class")]//strong',
            default=NotAvailable
        )
        obj_recommended_period = Title(
            '//div[contains(text(), "Durée recommandée") or contains(text(), "Recommended duration")]//strong',
            default=NotAvailable
        )

    def get_performance_url(self):
        js_script = CleanText('//script[@language="javascript"]')(self.doc)  # beurk
        # Extract performance URL from a string such as 'Product.init(false,"/particuliers..."'
        m = re.search(r'(/particuliers[^\"]+)', js_script)
        if m:
            return 'https://www.cpr-am.fr' + m.group(1)


class CprPerformancePage(LoggedPage, HTMLPage):
    def get_performance_history(self):
        # The positions of the columns depend on the age of the investment fund.
        # For example, if the fund is younger than 5 years, there will be not '5 ans' column.
        durations = [CleanText('.')(el) for el in self.doc.xpath('//div[contains(@class, "fpPerfglissanteclassique")]//th')]
        values = [CleanText('.')(el) for el in self.doc.xpath('//div[contains(@class, "fpPerfglissanteclassique")]//tr[td[text()="Fonds"]]//td')]
        matches = dict(zip(durations, values))
        # We do not fill the performance dictionary if no performance is available,
        # otherwise it will overwrite the data obtained from the JSON with empty values.
        perfs = {}
        for k, v in {1: '1 an', 3: '3 ans', 5: '5 ans'}.items():
            if matches.get(v):
                perfs[k] = percent_to_ratio(CleanDecimal.French(default=NotAvailable).filter(matches[v]))

        return perfs


DOCUMENT_TYPE_LABEL = {
    'RDC': DocumentTypes.STATEMENT,  # is this in label?
    'Relevé de situation': DocumentTypes.STATEMENT,
    'Relevé de compte': DocumentTypes.STATEMENT,
    'Bulletin': DocumentTypes.STATEMENT,
    'Sit Pat': DocumentTypes.REPORT,  # is this in label?
    'Situation de patrimoine': DocumentTypes.REPORT,
    'Avis': DocumentTypes.REPORT,
}


class EServicePage(LoggedPage, HTMLPage):
    def select_documents_tab(self):
        # force lowercase, it's not always the same case
        # and label to search for depends on child module
        edoc_td_xpath = '//td[matches(lower-case(text()),"e-documents|mes relevés|mes e-relevés|services en ligne")]'
        try:
            form = self.get_form(xpath=edoc_td_xpath + '/ancestor::form')
        except FormNotFound:
            self.logger.debug('no e-documents link, maybe we are already there')
            return

        doc_tab_id = (self.doc.xpath(edoc_td_xpath + '/ancestor::td/@id')[0])
        # warning: lxml returns its special string type which is incompatible with "re"
        assert re.search(':header:', doc_tab_id)
        form_tab = re.sub(':header:.*', '', doc_tab_id)

        for k, v in form.items():
            if v == 'coordPerso':
                form[k] = 'eService'
                break

        form['javax.faces.source'] = form_tab
        form['javax.faces.partial.event'] = 'click'
        form['javax.faces.partial.execute'] = '%s @component' % form_tab
        form['org.richfaces.ajax.component'] = form_tab
        self.logger.debug('selecting e-documents tab')
        form.submit()

    def show_more(self):
        form = self.get_form(xpath='//form[contains(@name, "consulterEReleves")]')

        try:
            # erehsbc: tout afficher
            # bnppere: afficher tous les e-documents
            button_el = form.el.xpath(
                './/input[matches(@value,"Tout afficher|Afficher tous")]'
            )[0]
        except IndexError:
            self.logger.debug('no "display all" button, everything already is displayed?')
            return
        buttonid = button_el.attrib['id']

        form['javax.faces.source'] = buttonid
        form['javax.faces.partial.event'] = 'click'
        form['javax.faces.partial.execute'] = '%s @component' % buttonid
        form['org.richfaces.ajax.component'] = buttonid
        self.logger.debug('showing all documents')
        form.submit()

    def get_error_message(self):
        return CleanText('//span[@class="operation-bloc-content-message-erreur-text"]')(self.doc)

    @method
    class iter_documents(TableElement):
        # Note: on this (partial) page, 'head' and 'items' are actually two different HTML tables.
        # It seems to confuse TableCell filter, thus we fetch data using XPath filter.
        # (As head_xpath is mandatory we provide its value nevertheless)
        item_xpath = '//div[contains(@id,"panelEReleves_body")]/div/table/tbody[contains(@id,"tb")]/tr[td]'
        head_xpath = '//div[contains(@id,"panelEReleves_body")]/table//th'

        class item(ItemElement):
            klass = Document

            obj_date = Date(CleanText(XPath('.//td[1]')), dayfirst=True)
            obj_label = Format('%s %s', CleanText(XPath('.//td[2]')), CleanText(XPath('.//td[1]')))
            obj_format = 'pdf'
            obj_url = AbsoluteLink('.//a')

            # Note: the id is constructed from the file name, which gives us some interesting information:
            # - Document date
            # Ex: RDCdirect_28112018link
            # Using _url_id instead of id because of duplicate IDs which are managed in the browser
            obj__url_id = CleanText(QueryValue(obj_url, 'titrePDF'), symbols='/ ')
            obj_type = MapIn(Field('label'), DOCUMENT_TYPE_LABEL, default=DocumentTypes.OTHER)


class CreditdunordPeePage(HTMLPage):
    def get_message(self):
        return CleanText('//div[@id="c127736"]')(self.doc)
