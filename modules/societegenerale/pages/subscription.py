# Copyright(C) 2010-2011 Jocelyn Jaubert
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

import hashlib
from datetime import datetime

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import BrowserURL, CleanText, DateTime, Env, Eval, Field, Format, Regexp
from woob.browser.pages import JsonPage, LoggedPage, RawPage
from woob.capabilities.bill import Document, DocumentTypes, Subscription
from woob.tools.date import parse_french_date

from .accounts_list import JsonBasePage


def parse_from_timestamp(date, **kwargs):
    # divide by 1000 because given value is a millisecond timestamp
    return datetime.fromtimestamp(int(date) / 1000)


class DocumentsPage(LoggedPage, JsonPage):
    def has_documents(self):
        return bool(self.doc["donnees"]["edocumentDto"]["listCleReleveDto"]) or bool(
            self.doc["donnees"]["edocumentDto"]["listCleRelevesAnnuellesDto"]
        )

    @method
    class iter_documents(DictElement):
        item_xpath = "donnees/edocumentDto/listCleReleveDto"

        def store(self, obj):
            """Ensure unicity of `obj_id`.

            This code enables `obj_id` to be unique when there
            are several docs with the exact same id.
            Sometimes we have two docs on the same date, so they have the same
            `label` thus the same `id`.
            (Note: there is another id in the document url that is unique but it is inconsistent, it
            changes on each session.)
            """
            _id = obj.id
            n = 1
            while _id in self.objects:
                n += 1
                _id = f"{obj.id}-{n}"
            obj.id = _id
            self.objects[obj.id] = obj
            return obj

        class item(ItemElement):
            klass = Document

            def obj_id(self):
                """Generate ID for the document.

                In the case of `docs-transverses`, the field `referenceTechniqueEncode` is always different
                from one request to the other.
                In that case, we use the hash(sha1) of the document label to have a stable, fixed `id`.
                """
                if Dict("codeGroupeProduit")(self) == "":
                    hash_label = hashlib.sha1(Field("label")(self).encode("utf-8")).hexdigest()
                    return "{}_{}".format(Env("subid")(self), hash_label)
                else:
                    return Format("%s_%s", Env("subid"), Dict("referenceTechniqueEncode"))(self)

            def obj_type(self):
                label = Field("label")(self)
                if label.startswith("Rapport"):
                    return DocumentTypes.REPORT
                else:
                    return DocumentTypes.STATEMENT

            obj_label = Format(
                "%s au %s", CleanText(Dict("labelReleve")), Eval(lambda x: x.strftime("%d/%m/%Y"), Field("date"))
            )
            obj_date = DateTime(CleanText(Dict("dateArrete")), parse_func=parse_from_timestamp, strict=False)
            obj_format = "pdf"
            # this url is stateful and has to be called when we are on
            # the right page with the right range of 3 months
            # else we get a 302 to /page-indisponible
            obj_url = BrowserURL(
                "pdf_page", id_tech=Dict("idTechniquePrestation"), ref_tech=Dict("referenceTechniqueEncode")
            )

    @method
    class iter_yearly_documents(DictElement):
        item_xpath = "donnees/edocumentDto/listCleRelevesAnnuellesDto"

        class item(ItemElement):
            klass = Document

            def obj_id(self):
                """Generate ID for the document.

                In the case of `docs-transverses`, the field `referenceTechniqueEncode` is always different
                from one request to the other.
                In that case, we use the hash(sha1) of the document label to have a stable, fixed `id`.
                """
                hash_label = hashlib.sha1(Field("label")(self).encode("utf-8")).hexdigest()
                return "{}_{}".format(Env("subid")(self), hash_label)

            def obj_type(self):
                label = Field("label")(self)
                if label.startswith("Imprimé Fiscal Unique"):
                    return DocumentTypes.CERTIFICATE
                else:
                    return DocumentTypes.STATEMENT

            obj_label = CleanText(Dict("labelReleve"))
            obj_date = DateTime(
                Regexp(CleanText(Dict("labelReleve")), r"(?:au|le) (\d{2}/\d{2}/\d{4})"),
                parse_func=parse_french_date,
                strict=False,
            )
            obj_format = "pdf"
            # this url is stateful and has to be called when we are on
            # the right page with the right range of 3 months
            # else we get a 302 to /page-indisponible
            obj_url = BrowserURL(
                "pdf_page", id_tech=Dict("idTechniquePrestation"), ref_tech=Dict("referenceTechniqueEncode")
            )


class RibPdfPage(LoggedPage, RawPage):
    pass


class SubscriptionsPage(JsonBasePage):
    @method
    class iter_subscription(DictElement):
        item_xpath = "donnees/listAbonnementEDocumentDto"

        class item(ItemElement):
            klass = Subscription

            obj__is_doc_transverse = Dict("infosProduitPrestation/documentTransverse", default=False)

            def obj__has_rib(self):
                return not Field("_is_doc_transverse")(self)

            def obj_label(self):
                if Field("_is_doc_transverse")(self):
                    return Dict("libellePrestation")(self)
                else:
                    return Format("%s %s", Dict("libellePrestation"), Field("id"))(self)

            def obj_id(self):
                if Field("_is_doc_transverse")(self):
                    return "docs-transverses"
                else:
                    return CleanText(Dict("numeroCompteFormate", default="NOTFOUND"), replace=[(" ", "")])(self)

            obj_subscriber = Env("subscriber")
            obj__internal_id = Dict("prestationIdTechnique", default="NOTFOUND")
