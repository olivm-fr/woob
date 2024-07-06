# Copyright(C) 2019 Powens
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
from datetime import timedelta
from urllib.parse import parse_qsl, urlparse

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.html import HasElement, Attr
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import (
    CleanDecimal, CleanText, CountryCode,
    Date, Env, Field, Format, Regexp,
)
from woob.browser.pages import HTMLPage, JsonPage, LoggedPage, RawPage
from woob.capabilities import NotAvailable
from woob.capabilities.address import PostalAddress
from woob.capabilities.base import empty
from woob.capabilities.bill import Bill, Subscription
from woob.capabilities.profile import Person
from woob.exceptions import BrowserIncorrectPassword


class LoginPage(HTMLPage):
    def login(self, username, password, lastname):
        form = self.get_form()
        form['username'] = username
        form['password'] = password
        form['submit'] = ''
        form['rememberMe'] = 'true'

        if 'lastname' in form:
            if not lastname:
                raise BrowserIncorrectPassword('Veuillez renseigner votre nom de famille.')
            form['lastname'] = lastname

        form.submit()

    def get_error_message(self):
        return CleanText('//p[@id="errorMessageContainer"]')(self.doc)

    def get_otp_config(self):
        # the body contains only js code with otp information
        otp_config = CleanText('//body')(self.doc)

        res = re.search(
            r"isSMS: (?P<is_sms>.*), contact: '(?P<contact>.*)', hasOtpExpired: (?P<expired>.*), maxOtpAttempts: (?P<max_attempts>.*), remainingOtpAttempts: (?P<remaining_attempts>.*), isFromRenewOtp",
            otp_config,
        )

        otp_data = {
            'is_sms': res.group('is_sms'),
            'contact': res.group('contact'),
            'expired': res.group('expired'),
            'max_attempts': res.group('max_attempts'),
            'remaining_attempts': res.group('remaining_attempts'),
        }
        return otp_data

    def send_2fa_code(self):
        form = self.get_form()
        # the body contains only js code with otp information
        otp_config = CleanText('//body')(self.doc)
        res = re.search(r"tel: \'(?P<phone_nbr>.*)\', email: \'(?P<email>.*)\'", otp_config)

        # We force sms 2FA because it is simplier for users, although email 2FA is more secure.
        if res.groupdict().get('phone_nbr'):
            contact = res.group('phone_nbr')
        elif res.groupdict().get('email'):
            contact = res.group('email')
        else:
            raise AssertionError("Unexpected SCA method, neither sms nor email found")

        self.browser.conversation_id = form['conversationId']
        self.browser.contact = form['maskedValue'] = contact
        form.submit()

    def get_execution(self):
        return Attr('//input[@name="execution"]', 'value')(self.doc)


class CallbackPage(HTMLPage):
    def has_id_and_access_token(self):
        fragments = dict(parse_qsl(urlparse(self.url).fragment))
        return 'id_token' in fragments and 'access_token' in fragments


class MaintenancePage(HTMLPage):
    def is_here(self):
        return HasElement('//title[contains(text(), "Notre site est indisponible")]')(self.doc)


class OauthPage(HTMLPage):
    pass


class HomePage(HTMLPage):
    pass


class ForgottenPasswordPage(HTMLPage):
    pass


class AccountPage(HTMLPage):
    def get_client_id(self):
        return Regexp(CleanText('//script[contains(text(), "clientId")]'), r'"clientId":"(.*?)"')(self.doc)


class SubscriberPage(LoggedPage, JsonPage):
    def get_subscriber(self):
        assert self.doc['type'] in ('INDIVIDU', 'ENTREPRISE'), "%s is unknown" % self.doc['type']

        if self.doc['type'] == 'INDIVIDU':
            subscriber_dict = self.doc
        elif self.doc['type'] == 'ENTREPRISE':
            subscriber_dict = self.doc['representantLegal']

        subscriber = '%s %s %s' % (
            subscriber_dict.get('civilite', ''),
            subscriber_dict['prenom'],
            subscriber_dict['nom'],
        )
        return subscriber.strip()

    def has_subscription_link(self):
        return HasElement(Dict('_links/comptesFacturation', default=None))(self.doc)

    def is_company(self):
        return self.doc['type'] == 'ENTREPRISE'

    @method
    class fill_personal_profile(ItemElement):
        obj_gender = CleanText(Dict('civilite'), default=NotAvailable)
        obj_firstname = CleanText(Dict('prenom'))
        obj_lastname = CleanText(Dict('nom'))
        # date in YYYY-MM-DD format
        obj_birth_date = Date(CleanText(Dict('dateNaissance')))
        obj_birth_place = CleanText(Dict('departementNaissance'))

    @method
    class fill_company_profile(ItemElement):
        obj_gender = CleanText(Dict('representantLegal/civilite'), default=NotAvailable)
        obj_firstname = CleanText(Dict('representantLegal/prenom'))
        obj_lastname = CleanText(Dict('representantLegal/nom'))
        obj_company_name = CleanText(Dict('raisonSociale'))


class SubscriptionDetail(LoggedPage, JsonPage):
    def get_label(self):
        phone_numbers = list(self.get_phone_numbers())
        account_id = self.params['id_account']

        label = str(account_id)

        if phone_numbers:
            label += " ({})".format(" - ".join(phone_numbers))
        return label

    def get_phone_numbers(self):
        for s in self.doc['items']:
            if 'numeroTel' in s:
                phone = re.sub(r'^\+\d{2}', '0', s['numeroTel'])
                yield ' '.join([phone[i: i + 2] for i in range(0, len(phone), 2)])


class SubscriptionPage(LoggedPage, JsonPage):
    @method
    class iter_subscriptions(DictElement):
        item_xpath = 'items'

        class item(ItemElement):
            klass = Subscription

            obj_id = Dict('id')


class ProfilePage(LoggedPage, JsonPage):
    @method
    class get_profile(ItemElement):
        klass = Person

        obj_email = Dict('emails/0/email', default=NotAvailable)
        obj_phone = Dict('telephones/0/numero', default=NotAvailable)

        class obj_postal_address(ItemElement):
            klass = PostalAddress

            obj_street = Dict('adressesPostales/0/rue', default=NotAvailable)
            obj_postal_code = Dict('adressesPostales/0/codePostal', default=NotAvailable)
            obj_city = Dict('adressesPostales/0/ville', default=NotAvailable)
            obj_country = Dict('adressesPostales/0/pays', default=NotAvailable)

            def obj_country_code(self):
                if not empty(Field('country')(self)):
                    return CountryCode(Field('country'), default=NotAvailable)(self)
                return NotAvailable


class MyDate(Date):
    """
    some date are datetime and contains date at GMT, and always at 22H or 23H
    but date inside PDF file is at GMT +1H or +2H (depends of summer or winter hour)
    so we add one day and skip time to get good date
    """

    def filter(self, txt):
        date = super(MyDate, self).filter(txt)
        if date:
            date += timedelta(days=1)
        return date


class DocumentPage(LoggedPage, JsonPage):
    def iter_subscription_invoices(self, subscription_id):
        for invoice_subscription in Dict(self.iter_documents.klass.item_xpath)(self.doc):
            if subscription_id != invoice_subscription['id']:
                continue
            for invoice in invoice_subscription['factures']:
                yield invoice

    def get_invoice_count(self, subscription_id):
        return len(list(self.iter_subscription_invoices(subscription_id)))

    @method
    class iter_documents(DictElement):
        item_xpath = 'data/consulterPersonne/factures/comptesFacturation'

        def find_elements(self):
            for invoice in self.page.iter_subscription_invoices(Env('subid')(self)):
                yield invoice

        class item(ItemElement):
            klass = Bill

            obj_id = Format('%s_%s', Env('subid'), Field('number'))
            obj_number = CleanText(Dict('id'))
            obj_total_price = CleanDecimal.SI(Dict('soldeApresFacture'))
            obj_url = CleanText(Dict('facturePDF/0/href'))
            obj_date = MyDate(Dict('dateFacturation'))
            obj_duedate = MyDate(Dict('dateLimitePaieFacture', default=NotAvailable), default=NotAvailable)
            obj_label = Format('Facture %s', Field('number'))
            obj_format = 'pdf'
            obj_currency = 'EUR'


class DocumentDownloadPage(LoggedPage, JsonPage):
    def get_download_url(self):
        return Dict('_actions/telecharger/action')(self.doc)


class DocumentFilePage(LoggedPage, RawPage):
    # since url of this file is almost the same than url of DocumentDownloadPage (which is a JsonPage)
    # we have to define it to avoid mismatching
    pass


class SendSMSPage(LoggedPage, HTMLPage):
    def post_message(self, receivers, content):
        form = self.get_form(name='formSMS')

        quota_text = CleanText('.//strong')(form.el)
        quota = int(re.search(r'(\d+) SMS gratuit', quota_text)[1])
        self.logger.info('quota: %d messages left', quota)
        if not quota:
            raise Exception('quota exceeded')

        form['fieldMsisdn'] = ';'.join(receivers)
        form['fieldMessage'] = content
        form.submit()
