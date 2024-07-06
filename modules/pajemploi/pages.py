# -*- coding: utf-8 -*-

# Copyright(C) 2020      Ludovic LANGE
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


from woob.capabilities.bill import DocumentTypes, Subscription, Document
from woob.browser.pages import (
    HTMLPage,
    PartialHTMLPage,
    RawPage,
    FormNotFound,
    pagination,
    NextPage,
)
from woob.exceptions import ParseError, BrowserUnavailable
from woob.browser.elements import method, ItemElement, TableElement
from woob.browser.filters.standard import (
    Filter,
    CleanText,
    Regexp,
    Env,
    Date,
    Format,
    Field,
    Eval,
    ItemNotFound,
)
from woob.browser.filters.html import Attr, Link, TableCell, FormValue
from woob.browser.filters.javascript import JSVar
from woob.tools.date import parse_french_date


class Child(Filter):
    def filter(self, el):
        return list(el[0].iterchildren())


class PajemploiPage(HTMLPage):
    @property
    def logged(self):
        return bool(CleanText('//a[contains(text(), "- Déconnexion")]')(self.doc))


class LoginPage(HTMLPage):
    def is_here(self):
        return not bool(CleanText('//a[contains(text(), "- Déconnexion")]')(self.doc))

    def login(self, username, password, captcha=None):
        form = self.get_form(id="frmConnexion")
        form["j_username"] = username
        form["j_password"] = password
        if captcha is not None:
            form['g-recaptcha-response'] = captcha
        form.submit()


class HomePage(PajemploiPage):
    pass


class ErrorMaintenancePage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable(CleanText().filter(self.doc.xpath('//div[@class="message"]')))


class EmployeesPage(PajemploiPage):
    @method
    class iter_subscriptions(TableElement):
        item_xpath = '//table[@id="tabSala"]/tbody/tr[has-class("NvTableauLigne1") or has-class("NvTableauLigne2")]'
        head_xpath = '//table[@id="tabSala"]/thead//th'

        col_nom = "Nom"
        col_prenom = "Prénom"
        col_pajemploiplus = "Pajemploi +"
        col_actif = "Actif"
        col_inactif = "Inactif"

        class item(ItemElement):
            klass = Subscription

            obj__nom1 = CleanText(TableCell("nom"))
            obj__nom2 = CleanText(Attr('.//input[contains(@id, ".nom")]', "value"))
            obj__prenom1 = CleanText(TableCell("prenom"))
            obj__prenom2 = CleanText(
                Attr('.//input[contains(@id, ".prenom")]', "value")
            )
            obj__internal_id = CleanText(
                Attr('.//input[contains(@id, ".salaEmplPk.noIntSala")]', "value")
            )
            obj__pseudo_siret = CleanText(
                Attr('.//input[contains(@id, ".salaEmplPk.psdoSirt")]', "value")
            )
            obj__date_creation = Date(
                CleanText(Attr('.//input[contains(@id, ".dtCreation")]', "value")),
                dayfirst=True,
            )
            obj_label = Format(
                "%s %s", CleanText(Field("_prenom1")), CleanText(Field("_nom1"))
            )
            # obj_subscriber = Env("subscriber")
            obj__type = "employee"
            obj__active = Eval(lambda x: x[0].checked, (Child(TableCell("actif"))))
            obj_id = Field("_internal_id")


class TaxCertificatesPage(PajemploiPage):
    @pagination
    def iter_documents(self, subscription):
        next_page = None
        try:
            form = self.get_form('//input[@id="id_btn_valider"]/parent::form')
            next_yr = self.doc.xpath(
                '//select[@name="annee"]/option[@selected]/following-sibling::option'
            )
            if len(next_yr):
                form["annee"] = Attr(".", "value")(next_yr[0])
                next_page = form.request
        except FormNotFound:
            pass

        empty = self.doc.xpath('//text()[contains(., "Aucun volet social")]')

        if not empty:

            frm = self.doc.xpath('//input[@id="modeGarde"]/parent::form')[0]

            d = Document()

            d._annee = CleanText(Attr('.//input[@id="annee"]', "value"))(frm)
            d.id = "%s_%s" % (subscription.id, d._annee)
            d.date = parse_french_date("%s-12-31" % d._annee)
            d.label = "Attestation fiscale %s" % (d._annee)
            d.type = DocumentTypes.CERTIFICATE
            d.format = "pdf"
            d.url = Link("./table//div/a")(frm)

            yield d

        if next_page:
            raise NextPage(next_page)


class TaxCertificateDownloadPage(RawPage):
    pass


class PayslipDownloadPage(RawPage):
    pass


class DeclarationSetupPage(PajemploiPage):
    def get_data(self, subscription):
        debut_mois_periode = self.doc.xpath('//select[@id="debutMoisPeriode"]')
        debut_annee_periode = self.doc.xpath('//select[@id="debutAnneePeriode"]')
        fin_mois_periode = self.doc.xpath('//select[@id="finMoisPeriode"]')
        fin_annee_periode = self.doc.xpath('//select[@id="finAnneePeriode"]')

        data = {
            "activite": "T",
            "paye": "false",
            "noIntSala": subscription.id,
            "order": "periode",
            "byAsc": "false",
        }
        if debut_mois_periode:
            data["dtDebMois"] = min(debut_mois_periode[0].value_options)
        if debut_annee_periode:
            data["dtDebAnnee"] = min(debut_annee_periode[0].value_options)
        if fin_mois_periode:
            data["dtFinMois"] = max(fin_mois_periode[0].value_options)
        if fin_annee_periode:
            data["dtFinAnnee"] = max(fin_annee_periode[0].value_options)
        return data


class DeclarationListPage(PartialHTMLPage):
    @method
    class iter_documents(TableElement):
        item_xpath = '//table[@id="tabVsTous"]//tr[has-class("NvTableauLigne1") or has-class("NvTableauLigne2")]'
        head_xpath = '//table[@id="tabVsTous"]//th'

        class item(ItemElement):
            klass = Document

            obj__refdoc = Regexp(
                Attr(".", "onclick", default=""),
                r"\('refdoc'\)\.value='([^\']+)'",
                default=None,
            )
            obj__norng = Regexp(
                Attr(".", "onclick", default=""),
                r"\('norng'\)\.value='([^\']+)'",
                default=None,
            )
            obj_id = Format("%s_%s", Env("subscription_id"), Field("_refdoc"))


class MonthlyReportDownloadPage(RawPage):
    pass


class RegistrationRecordDownloadPage(RawPage):
    pass


class CotisationsDownloadPage(RawPage):
    pass


class AjaxDetailSocialInfoPage(PartialHTMLPage):
    pass


class DeclarationDetailPage(PajemploiPage):
    def on_load(self):
        js = self.doc.xpath(
            '//script[@language="Javascript"][contains(text(), "function selectRecherche")]'
        )
        div = self.doc.xpath('//div[@id="cont_onglet1"]')
        if js and div:
            service_ajax = Regexp(
                CleanText("."),
                r"pageAjax=\"cont_onglet1\";\W+serviceAjax = \"([^\"]+)\";",
                default=None,
            )(js[0])
            parametre = Regexp(
                CleanText("."),
                r"pageAjax=\"cont_onglet1\";\W+serviceAjax = \"[^\"]+\";\W+parametre = \"([^\"]+)\";",
                default=None,
            )(self.doc)
            self.browser.session.headers.update(
                {"Content-Type": "application/x-www-form-urlencoded"}
            )
            pg = self.browser.open(service_ajax, data=parametre)
            if hasattr(pg, "page") and pg.page:
                self._doc2 = pg.page.doc

    def get_date(self):
        date = None
        dt_elt = self.doc.xpath(
            '//td[text()="Période d\'emploi"]/following-sibling::td'
        )
        if not dt_elt:
            dt_elt = self._doc2.xpath(
                '//td[text()="Période d\'emploi"]/following-sibling::td'
            )
        if dt_elt:
            date = Date(
                Regexp(CleanText("."), r"au (\d{2}\/\d{2}\/\d{4})"), dayfirst=True
            )(dt_elt[0])
        else:
            raise ParseError()
        return date

    def iter_documents(self, proto_doc, subscription):
        date = self.get_date()

        script = CleanText('//script[not(@src)][contains(text(), "traitementEffectue")]')
        try:
            traitementEffectue = JSVar(script, var='traitementEffectue')(self.doc)
            presAnnule = JSVar(script, var='presAnnule')(self.doc)
        except ItemNotFound:
            traitementEffectue = True
            presAnnule = 0

        # Bulletin de salaire
        frm = self.doc.xpath('//form[@name="formBulletinSalaire"]')
        if frm and traitementEffectue and (presAnnule == 0):
            bs = Document()
            bs.id = "%s_%s" % (proto_doc.id, "bs")
            bs.date = date
            bs.format = "pdf"
            bs.type = DocumentTypes.STATEMENT
            bs.label = "Bulletin de salaire %s %s" % (subscription.label, date.strftime("%d/%m/%Y"))
            bs.url = Attr(".", "action")(frm[0])
            bs._ref = FormValue('./input[@id="ref"]')(frm[0])
            yield bs

        # Relevé mensuel
        frm = self.doc.xpath('//form[@name="formReleveMensuel"]')
        if frm and traitementEffectue and (presAnnule == 0):
            rm = Document()
            rm.id = "%s_%s" % (proto_doc.id, "rm")
            rm.date = date
            rm.format = "pdf"
            rm.type = DocumentTypes.STATEMENT
            rm.label = "Relevé mensuel %s %s" % (subscription.label, date.strftime("%d/%m/%Y"))
            rm.url = Attr(".", "action")(frm[0])
            rm._need_refresh_previous_page = True
            yield rm

        # Certificat d'Enregistrement
        frm = self.doc.xpath('//form[@name="formGenererPDF"]')
        if frm:
            ce = Document()
            ce.id = "%s_%s" % (proto_doc.id, "ce")
            ce.date = date
            ce.format = "pdf"
            ce.type = DocumentTypes.CERTIFICATE
            ce.label = "Certificat d'enregistrement %s %s" % (subscription.label, date.strftime("%d/%m/%Y"))
            ce.url = Attr(".", "action")(frm[0])
            ce._need_refresh_previous_page = True
            yield ce

        # Cotisations
        frm = self.doc.xpath('//form[@name="formDecomptCoti"]')
        if frm:
            dc = Document()
            dc.id = "%s_%s" % (proto_doc.id, "dc")
            dc.date = date
            dc.format = "pdf"
            dc.type = DocumentTypes.STATEMENT
            dc.label = "Décompte de cotisations %s %s" % (subscription.label, date.strftime("%d/%m/%Y"))
            dc.url = Attr(".", "action")(frm[0])
            yield dc
