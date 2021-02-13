# -*- coding: utf-8 -*-

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

from __future__ import unicode_literals

import hashlib
import re

from weboob.browser.pages import HTMLPage, LoggedPage, pagination, JsonPage, RawPage
from weboob.browser.filters.standard import (
    CleanText, Env, Field, Regexp, Format, Date, Coalesce,
)
from weboob.browser.filters.json import Dict
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.browser.filters.html import Attr
from weboob.browser.filters.javascript import JSVar, JSValue
from weboob.capabilities.address import PostalAddress
from weboob.capabilities.bill import DocumentTypes, Document, Subscription
from weboob.capabilities.profile import Person
from weboob.capabilities.base import NotAvailable
from weboob.tools.date import parse_french_date


class LoginAccessPage(HTMLPage):

    def __init__(self, *args, **kwargs):
        super(LoginAccessPage, self).__init__(*args, **kwargs)
        self.url_contexte = None
        self.url_login_mot_de_passe = None

    def login(self, login, password, url):
        form = self.get_form(id='formulairePrincipal')
        form.url = url
        form['spi'] = login
        form['pwd'] = password
        form.submit()

    def on_load(self):
        self.url_contexte = JSVar(
            CleanText("//script[contains(text(), 'urlContexte')]"), var="urlContexte"
        )(self.doc)
        self.url_login_mot_de_passe = JSVar(
            CleanText("//script[contains(text(), 'urlLoginMotDePasse')]"),
            var="urlLoginMotDePasse",
        )(self.doc)


class MessageResultPage(HTMLPage):
    """Consolidate behaviour of message-passing pages."""
    def on_load(self):
        js = self.doc.xpath("//script")
        if js:
            parameters = JSValue(CleanText("."), nth=0)(js[0])
            args = parameters.split(",")
            assert len(args) == 2
            self.message = args[0]
            self.value = args[1]

    def handle_message(self):
        if self.message == "ctx":
            if self.value == "LMDP":
                return True
        elif self.message == "ok":
            return self.value
        return False


class GetContextePage(MessageResultPage):
    pass


class LoginAELPage(MessageResultPage):
    def is_login_successful(self):
        is_login_ok = CleanText('//head/title')(self.doc) == 'lmdp'
        if not is_login_ok:
            return 'wrong login'

        state = Regexp(CleanText('//script'), r"parent.postMessage\('(.*?),.*\)")(self.doc)
        if state != 'ok':
            return 'wrong password'

    def get_redirect_url(self):
        return Regexp(CleanText('//body/script'), r"postMessage\('ok,(.*)',")(self.doc)


class ImpotsPage(HTMLPage):
    @property
    def logged(self):
        return bool(CleanText('//button[@id="accederdeconnexion"]')(self.doc))


class HomePage(ImpotsPage):
    pass


class NoDocumentPage(LoggedPage, RawPage):
    pass


class ErrorDocumentPage(LoggedPage, RawPage):
    pass


class ThirdPartyDocPage(LoggedPage, JsonPage):
    @method
    class get_third_party_doc(ItemElement):
        klass = Document

        obj_id = Format('%s_%s', Dict('spiDec1'), Dict('dateNaisDec1'))
        obj_format = 'json'
        obj_label = 'Déclaration par un tiers'
        obj_type = DocumentTypes.OTHER

        def obj_url(self):
            return self.page.url


class ProfilePage(LoggedPage, HTMLPage):
    def get_documents_link(self):
        return self.doc.xpath('//a[contains(@title, "déclarations")]/@href')[0]

    def get_bills_link(self):
        return self.doc.xpath('//a[contains(@title, "résumé")]/@href')[0]

    @method
    class get_subscriptions(ListElement):
        class item(ItemElement):
            klass = Subscription

            obj_subscriber = Format('%s %s', CleanText('//span[@id="prenom"]'), CleanText('//span[@id="nom"]'))
            obj_id = Regexp(CleanText('//span[contains(text(), "N° fiscal")]'), r'N° fiscal : (\d+)')
            obj_label = Field('id')

    @method
    class get_profile(ItemElement):
        klass = Person

        obj_name = Format('%s %s', Field('firstname'), Field('lastname'))
        obj_firstname = CleanText('//span[@id="prenom"]')
        obj_lastname = CleanText('//span[@id="nom"]')
        obj_email = CleanText('//div[span[contains(text(), "Adresse électronique")]]/following-sibling::div/span')
        obj_mobile = CleanText('//div[span[text()="Téléphone portable"]]/following-sibling::div/span', default=NotAvailable)
        obj_phone = CleanText('//div[span[text()="Téléphone fixe"]]/following-sibling::div/span', default=NotAvailable)
        obj_birth_date = Date(CleanText('//span[@id="datenaissance"]'), parse_func=parse_french_date)

        class obj_postal_address(ItemElement):
            klass = PostalAddress

            obj_full_address = Env('full_address', default=NotAvailable)
            obj_street = Env('street', default=NotAvailable)
            obj_postal_code = Env('postal_code', default=NotAvailable)
            obj_city = Env('city', default=NotAvailable)

            def parse(self, obj):
                full_address = CleanText('//span[@id="adressepostale"]')(self)
                m = re.search(r'([\w ]+) (\d{5}) ([\w ]+)', full_address)
                if not m:
                    self.env['full_address'] = full_address
                else:
                    street, postal_code, city = m.groups()
                    self.env['street'] = street
                    self.env['postal_code'] = postal_code
                    self.env['city'] = city


class DocumentsPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class iter_documents(ListElement):
        item_xpath = '//ul[has-class("documents")]/li'

        def next_page(self):
            previous_year = CleanText(
                '//li[has-class("blocAnnee") and has-class("selected")]/following-sibling::li[1]/a',
                children=False
            )(self.page.doc)

            # only if previous_year is not None and different from current year,
            # else we return to page with current year and fall into infinite loop
            if previous_year:
                previous_year = int(Regexp(None, r'(\d{4})').filter(previous_year))

                current_year = int(Regexp(CleanText(
                    '//li[has-class("blocAnnee") and has-class("selected")]/a',
                    children=False
                ), r'(\d{4})')(self.page.doc))

                if previous_year >= current_year:
                    # if previous year is 'something 2078' website return page of current year
                    # previous_year has to be nothing but digit
                    # don't return anything to not fall into infinite loop, but something bad has happened
                    self.logger.error(
                        "pagination loop, previous_year: %s pagination is unexpectedly superior or equal to current_year: %s",
                        previous_year, current_year
                    )
                    return

                return self.page.browser.documents.build(params={'n': previous_year})

        class item(ItemElement):
            klass = Document

            obj__idEnsua = Attr('.//form/input[@name="idEnsua"]', 'value')  # can be 64 or 128 char length

            def obj_id(self):
                # hash _idEnsua to reduce his size at 32 char
                hash = hashlib.sha1(Field('_idEnsua')(self).encode('utf-8')).hexdigest()
                return '%s_%s' % (Env('subid')(self), hash)

            obj_date = Date(Env('date'))
            obj_label = Env('label')
            obj_type = DocumentTypes.INCOME_TAX
            obj_format = 'pdf'
            obj_url = Format('/enp/ensu/Affichage_Document_PDF?idEnsua=%s', Field('_idEnsua'))

            def parse(self, el):
                label_ct = CleanText('./div[has-class("texte")][has-class("visible-xs")]')
                date = Regexp(label_ct, r'le ([\w\/]+?),', default=NotAvailable)(self)
                self.env['label'] = label_ct(self)

                if not date:
                    # exclude n° to not take n° 2555123456 as year 2555
                    # or if there is absolutely no date written in html for this document
                    # when label is "Mise en demeure de payer" for example
                    # take just the year in current page
                    year = Coalesce(
                        Regexp(label_ct, r'\b(\d{4})\b', default=NotAvailable),
                        CleanText(
                            '//li[has-class("blocAnnee") and has-class("selected")]/a',
                            children=False, default=NotAvailable,
                        )
                    )(self)

                    if 'sur les revenus de' in self.env['label']:
                        # this kind of document always appear un july, (but we don't know the day)
                        date = '%s-07-01' % year
                    else:
                        date = '%s-01-01' % year
                self.env['date'] = date
