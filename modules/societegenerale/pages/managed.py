# Copyright(C) Ludovic LANGE
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
from base64 import b64decode
from datetime import datetime
from urllib.parse import quote_plus

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, Env, Field, Map
from woob.browser.pages import HTMLPage
from woob.capabilities.base import NotAvailable
from woob.capabilities.bill import Document, DocumentTypes
from woob.exceptions import BrowserUnavailable

from .accounts_list import JsonBasePage


DOC_MAPPING = {
    "rapportGestion": DocumentTypes.REPORT,
    "lettresMensuelles": DocumentTypes.REPORT,
    "courriers": DocumentTypes.NOTICE,
}


class ManagedDocTypes(JsonBasePage):

    def on_load(self):
        if Dict("commun/statut")(self.doc).upper() == "NOK":
            reason = Dict("commun/raison")(self.doc)
            # action = Dict("commun/action")(self.doc)

            if "MSG-HAB-001" in reason:
                self.logger.warning('"%s" - auth insuffisante', reason)
                raise BrowserUnavailable()

            super().on_load()

    def iter_document_types(self):
        for doctype in DOC_MAPPING.keys():
            if Dict(f"donnees/{doctype}", default=False)(self.doc):
                yield doctype


class ManagedVerify(JsonBasePage):
    pass


class ManagedAvailableDates(JsonBasePage):
    def dates(self):
        return Dict("donnees/dates", default=[])(self.doc)


class ManagedIndex(HTMLPage):
    pass


class ManagedDocument(JsonBasePage):
    @method
    class iter_documents(DictElement):
        item_xpath = "donnees"

        class item(ItemElement):
            klass = Document

            obj_label = Dict("titre")
            obj_format = "pdf"
            obj_type = Map(Env("type_doc"), DOC_MAPPING, DocumentTypes.OTHER)
            obj_has_file = True
            obj__reporting_id = CleanText(Dict("reportingId"), default="null")  # Fixed value
            obj__reporting_key = CleanText(Dict("reportingKey"), default="null")  # varies with session
            obj__seal = CleanText(Dict("seal"), default="null")

            def obj_id(self):
                """Generate ID for the document."""
                hash_label = hashlib.sha1(Field("_reporting_id")(self).encode("utf-8")).hexdigest()
                return "{}_{}".format(Env("subid")(self), hash_label)

            def obj_url(self):
                url_prefix = self.page.browser.absurl("/com/icd-web/tor" + Dict("urlPrefix")(self))
                url = (
                    f"{url_prefix}?"
                    + f"b64e4000_reportingId={quote_plus(Field('_reporting_id')(self))}&"
                    + f"b64e4000_reportingKey={quote_plus(Field('_reporting_key')(self))}&"
                    + f"b64e4000_seal={quote_plus(Field('_seal')(self))}"
                )
                return url

            def obj_date(self):
                """It seems that the opaque `reportingId` has the date of the document encoded inside, as the last '|'-separated field."""
                dt = NotAvailable
                try:
                    decoded = b64decode(Field("_reporting_id")(self))
                    partition = decoded.rpartition(b"|")
                    date = partition[2].decode()
                    if len(date) == 12:
                        dt = datetime.strptime(date, "%y%m%d%H%M%S")
                except ValueError:
                    self.logger.exception("Failed to extract document datetime from `reportingId`, please investigate")
                    pass
                return dt
