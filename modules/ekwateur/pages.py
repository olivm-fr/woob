# Copyright(C) 2018      Phyks (Lucas Verney)
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

from woob.browser.elements import ItemElement, ListElement, TableElement, method
from woob.browser.filters.html import AbsoluteLink, Attr, Link, TableCell, XPath
from woob.browser.filters.standard import CleanDecimal, CleanText, Currency, Date, Env, Format, Regexp, Slugify
from woob.browser.pages import HTMLPage
from woob.capabilities.address import PostalAddress
from woob.capabilities.base import NotAvailable
from woob.capabilities.bill import Bill, Document, DocumentTypes, Subscription
from woob.capabilities.profile import Person
from woob.tools.date import parse_french_date


class LoginPage(HTMLPage):
    def do_login(self, login, password):
        form = self.get_form(nr=0)
        form["_username"] = login
        form["_password"] = password
        form.submit()


class EkwateurPage(HTMLPage):
    @property
    def logged(self):
        return bool(self.doc.xpath('//*[has-class("menu__user__infos")]'))


class BillsPage(EkwateurPage):
    @method
    class get_subscriptions(ListElement):
        item_xpath = '//a[has-class("nom_contrat")]'

        class item(ItemElement):
            klass = Subscription
            obj_subscriber = CleanText('(//h1[has-class("menu__user__infos__title")])[1]')

            def obj_id(self):
                return Link(".")(self).split("/")[-1]

            def obj_label(self):
                name = Attr(".", "data-nomcontrat", default=None)(self)
                if not name:
                    name = CleanText(".")(self)
                return name

    @method
    class get_bills(TableElement):
        item_xpath = '//table[@id="dataHistorique"]/tbody/tr'
        head_xpath = '//table[@id="dataHistorique"]/thead/tr/td/text()'

        col_date = "Date"
        col_type = "Type"
        col_amount = "Montant"
        col_status = "Statut"

        class item(ItemElement):
            klass = Bill

            obj_id = Format(
                "facture-%s-%s-%s#%s",
                Slugify(CleanText(TableCell("date"))),
                Slugify(CleanText(TableCell("amount"))),
                Slugify(CleanText(TableCell("type"))),
                Env("sub_id"),
            )
            obj_url = AbsoluteLink("./td[5]//a", default=NotAvailable)
            obj_date = Date(CleanText(TableCell("date")), dayfirst=True)
            obj_label = Format(
                "%s %s %s", CleanText(TableCell("type")), CleanText(TableCell("amount")), CleanText(TableCell("date"))
            )
            obj_type = DocumentTypes.BILL
            obj_price = CleanDecimal(TableCell("amount"), replace_dots=True)
            obj_currency = Currency(TableCell("amount"))
            obj_duedate = Date(
                Regexp(CleanText(TableCell("status")), r"le (\d+)/(\d+)/(\d+)", r"\1/\2/\3"), dayfirst=True
            )

            def obj_format(self):
                if self.obj_url(self):
                    return "pdf"
                return NotAvailable

            def obj_income(self):
                if self.obj_price(self) < 0:
                    return True
                return False


class DocumentsPage(EkwateurPage):
    @method
    class get_documents(TableElement):
        item_xpath = '//table[@id="otherDocuments"]/tbody/tr'
        head_xpath = '//table[@id="otherDocuments"]/thead/tr/td/text()'

        col_date = "Date"
        col_type = "Type"

        ignore_duplicate = True

        class item(ItemElement):
            klass = Document

            obj_date = Date(CleanText(TableCell("date")), dayfirst=True)
            obj_format = "pdf"
            obj_label = CleanText(TableCell("type"))
            obj_url = AbsoluteLink("./td[3]//a", default=NotAvailable)
            obj_id = Format(
                "doc-%s-%s#%s",
                Slugify(CleanText(TableCell("date"))),
                Slugify(CleanText(TableCell("type"))),
                Env("sub_id"),
            )

    def get_justificatif(self, sub_id):
        doc = Document()
        doc.id = "doc-justificatif#%s" % sub_id
        doc.format = "pdf"
        doc.date = NotAvailable
        doc.label = "Justificatif de domicile"
        doc.url = "https://mon-espace.ekwateur.fr/client/justificatif_de_domicile"
        yield doc

    def get_cgv(self, sub_id):
        CGV = Document()
        CGV.id = "doc-CGV#%s" % sub_id
        CGV.format = "pdf"
        CGV.date = NotAvailable
        CGV.label = "CGV électricité"
        CGV.type = "cgv"
        for item in XPath('.//div[has-class("table__foot__adobe-reader__link")]//a')(self.doc):
            if "CGV" in item.text:
                CGV.url = item.attrib["href"]
        yield CGV


class ProfilePage(EkwateurPage):
    @method
    class get_profile(ItemElement):
        klass = Person

        obj_name = Env("name", default=NotAvailable)
        obj_gender = Env("gender", default=NotAvailable)
        obj_company_name = CleanText('//p[contains(text(),"Raison sociale")]/b', default=NotAvailable)
        obj_birth_date = Date(
            CleanText(
                '//span[div/span/text()="Contact de facturation"]/following-sibling::p[contains(text(),"Date de naissance")]/b',
            ),
            parse_func=parse_french_date,
        )

        obj_phone = CleanText(
            '//span[div/span/text()="Contact de facturation"]/following-sibling::p[contains(text(),"Tél. port")]/b',
            default=NotAvailable,
        )
        obj_email = CleanText('//p[contains(text(),"email de connexion")]/b', default=NotAvailable)

        def parse(self, obj):
            full_name = CleanText('//span[div/span/text()="Contact de facturation"]/following-sibling::p[1]/b[1]')(self)
            m = re.search(r"(M\.|Mme) ([\w \-]+)", full_name)
            if not m:
                self.env["name"] = full_name
            else:
                gender, name = m.groups()
                self.env["gender"] = gender
                self.env["name"] = name

        class obj_postal_address(ItemElement):
            klass = PostalAddress

            def parse(self, obj):
                full_address = CleanText('//span[div/span/text()="Adresse de facturation"]/following-sibling::p[2]')(
                    self
                )
                self.env["full_address"] = full_address
                m = re.search(r"(\d{1,4}.*) (\d{5}) (.*)", full_address)
                if m:
                    street, postal_code, city = m.groups()
                    self.env["street"] = street
                    self.env["postal_code"] = postal_code
                    self.env["city"] = city

            obj_full_address = Env("full_address", default=NotAvailable)
            obj_street = Env("street", default=NotAvailable)
            obj_postal_code = Env("postal_code", default=NotAvailable)
            obj_city = Env("city", default=NotAvailable)
            obj_country = Env("country", default=NotAvailable)
