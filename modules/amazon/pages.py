# -*- coding: utf-8 -*-

# Copyright(C) 2017      Théo Dorée
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

from weboob.browser.pages import HTMLPage, LoggedPage, FormNotFound, PartialHTMLPage, pagination
from weboob.browser.elements import ItemElement, ListElement, method
from weboob.browser.filters.html import Link, Attr
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Env, Regexp, Format,
    Field, Currency, RegexpError, Date, Async, AsyncLoad,
    Coalesce,
)
from weboob.capabilities.bill import DocumentTypes, Bill, Subscription
from weboob.capabilities.base import NotAvailable
from weboob.tools.date import parse_french_date


class HomePage(HTMLPage):
    def get_login_link(self):
        return self.doc.xpath('//a[./span[contains(., "%s")]]/@href' % self.browser.L_SIGNIN)[0]

    def get_panel_link(self):
        return Link('//a[contains(@href, "homepage.html") and has-class(@nav-link)]')(self.doc)


class PanelPage(LoggedPage, HTMLPage):
    def get_sub_link(self):
        return CleanText('//a[@class="ya-card__whole-card-link" and contains(@href, "cnep")]/@href')(self.doc)


class SecurityPage(HTMLPage):
    def get_otp_type(self):
        # amazon send us otp in two cases:
        # - if it's the first time we connect to this account for an ip => manage it normally
        # - if user has activated otp in his options => raise ActionNeeded, an ask user to deactivate it
        form = self.get_form(xpath='//form[.//h1]')
        url = form.url.replace(self.browser.BASEURL, '')

        # verify: this otp is sent by amazon when we connect to the account for the first time from a new ip or computer
        # /ap/signin: this otp is a user activated otp which is always present
        assert url in ('verify', '/ap/signin'), url
        return url

    def get_otp_message(self):
        return CleanText('//div[@class="a-box-inner"]/p')(self.doc)

    def send_code(self):
        form = self.get_form()
        if form.el.attrib.get('id') == 'auth-mfa-form':
            # when code is sent by sms, server send it automatically, nothing to do here
            return

        if 'sms' in self.doc.xpath('//div[@data-a-input-name="option"]//input[@name="option"]/@value'):
            form['option'] = 'sms'

        # by email, we have to confirm code sending
        form.submit()

    def get_response_form(self):
        try:
            form = self.get_form(id='auth-mfa-form')
            return {'form': form, 'style': 'userDFA'}
        except FormNotFound:
            form = self.get_form(nr=0)
            return {'form': form, 'style': 'amazonDFA'}

    def get_captcha_url(self):
        return Attr('//img[@alt="captcha"]', 'src', default=NotAvailable)(self.doc)

    def resolve_captcha(self, captcha_response):
        form = self.get_form('//form[@action="verify"]')
        form['cvf_captcha_input'] = captcha_response
        form.submit()

    def has_form_verify(self):
        if self.doc.xpath('//form[@action="verify"]'):
            return True


class ApprovalPage(HTMLPage, LoggedPage):
    def get_msg_app_validation(self):
        msg = CleanText('//span[has-class("transaction-approval-word-break")]')
        sending_address = CleanText('//div[@class="a-row"][1]')
        msg = Format('%s %s', msg, sending_address)
        return msg(self.doc)

    def get_link_app_validation(self):
        return Link('//a[contains(text(), "Click here to refresh the page")]')(self.doc)


class LanguagePage(HTMLPage):
    pass


class LoginPage(HTMLPage):
    def login(self, login, password, captcha=None):
        form = self.get_form(name='signIn')

        form['email'] = login
        form['password'] = password
        form['rememberMe'] = "true"

        if captcha:
            form['guess'] = captcha
        form.submit()

    def has_captcha(self):
        return self.doc.xpath('//div[@id="image-captcha-section"]//img[@id="auth-captcha-image"]/@src')

    def get_response_form(self):
        try:
            form = self.get_form(id='auth-mfa-form')
            return form
        except FormNotFound:
            form = self.get_form(nr=0)
            return form

    def get_error_message(self):
        return CleanText('//div[@id="auth-error-message-box"]')(self.doc)


class PasswordExpired(HTMLPage):
    def get_message(self):
        return CleanText('//form//h2')(self.doc)


class SubscriptionsPage(LoggedPage, HTMLPage):
    @method
    class get_item(ItemElement):
        klass = Subscription

        def obj_subscriber(self):
            try:
                return Regexp(CleanText('//div[contains(@class, "a-fixed-right-grid-col")]'), self.page.browser.L_SUBSCRIBER)(self)
            except RegexpError:
                return self.page.browser.username

        obj_id = 'amazon'

        def obj_label(self):
            return self.page.browser.username


class HistoryPage(LoggedPage, HTMLPage):
    def get_b2b_group_key(self):
        return Attr(
            '//select[@name="selectedB2BGroupKey"]/option[contains(text(), "Afficher toutes les commandes")]',
            'value',
            default=None
        )(self.doc)


class DocumentsPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class iter_documents(ListElement):
        item_xpath = '//div[contains(@class, "order") and contains(@class, "a-box-group")]'

        def next_page(self):
            return Link('//ul[@class="a-pagination"]/li[@class="a-last"]/a')(self)

        class item(ItemElement):
            klass = Bill
            load_details = Field('_pre_url') & AsyncLoad

            obj__simple_id = Coalesce(
                CleanText('.//span[contains(text(), "N° de commande")]/following-sibling::span', default=NotAvailable),
                CleanText('.//span[contains(text(), "Order")]/following-sibling::span'),
            )

            obj_id = Format('%s_%s', Env('subid'), Field('_simple_id'))

            obj__pre_url = Format('/gp/shared-cs/ajax/invoice/invoice.html?orderId=%s&relatedRequestId=%s&isADriveSubscription=&isHFC=',
                                  Field('_simple_id'), Env('request_id'))
            obj_label = Format('Facture %s', Field('_simple_id'))
            obj_type = DocumentTypes.BILL

            def obj_date(self):
                # The date xpath changes depending on the kind of order
                return Coalesce(
                    Date(CleanText('.//div[has-class("a-span4") and not(has-class("recipient"))]/div[2]'), parse_func=parse_french_date, dayfirst=True, default=NotAvailable),
                    Date(CleanText('.//div[has-class("a-span3") and not(has-class("recipient"))]/div[2]'), parse_func=parse_french_date, dayfirst=True, default=NotAvailable),
                    Date(CleanText('.//div[has-class("a-span2") and not(has-class("recipient"))]/div[2]'), parse_func=parse_french_date, dayfirst=True, default=NotAvailable),
                )(self)

            def obj_price(self):
                # Some orders, audiobooks for example, are paid using "audio credits", they have no price or currency
                currency = Env('currency')(self)
                return CleanDecimal(
                    './/div[has-class("a-col-left")]//span[has-class("value") and contains(., "%s")]' % currency,
                    replace_dots=currency == 'EUR', default=NotAvailable
                )(self)

            def obj_currency(self):
                currency = Env('currency')(self)
                return Currency(
                    './/div[has-class("a-col-left")]//span[has-class("value") and contains(., "%s")]' % currency,
                    default=NotAvailable
                )(self)

            def obj_url(self):
                async_page = Async('details').loaded_page(self)
                url = Coalesce(
                    Link('//a[@class="a-link-normal" and contains(text(), "Invoice")]', default=NotAvailable),
                    Link('//a[contains(text(), "Order Details")]', default=NotAvailable),
                    default=NotAvailable,
                )(self)
                if not url:
                    url = Coalesce(
                        Link('//a[contains(@href, "download")]|//a[contains(@href, "generated_invoices")]', default=NotAvailable),
                        Link('//a[contains(text(), "Récapitulatif de commande")]', default=NotAvailable),
                    )(async_page.doc)
                return url

            def obj_format(self):
                if 'summary' in Field('url')(self):
                    return 'html'
                return 'pdf'


class DownloadDocumentPage(LoggedPage, PartialHTMLPage):
    pass
