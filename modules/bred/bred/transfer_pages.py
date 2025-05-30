# Copyright(C) 2020 Guillaume Risbourg
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

import re
from datetime import date

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanDecimal, CleanText, Currency, Format, Regexp
from woob.browser.pages import JsonPage, LoggedPage
from woob.capabilities.bank import Recipient


class ListAuthentPage(LoggedPage, JsonPage):
    def get_handled_auth_methods(self):
        # Order in auth_methods is important, the first method we encouter
        # is the strong authentification we are going to do.
        auth_methods = ("password", "otp", "sms", "notification")
        for auth_method in auth_methods:
            if Dict("content/%s" % auth_method)(self.doc):
                return auth_method


class EmittersListPage(LoggedPage, JsonPage):
    def can_account_emit_transfer(self, account_id):
        code = Dict("erreur/code")(self.doc)
        if code == "90624":
            # Not the owner of the account:
            # Nous vous précisons que votre pouvoir ne vous permet pas
            # d'effectuer des virements de ce type au débit du compte sélectionné.
            return False
        elif code == "90600":
            # "Votre demande de virement ne peut être prise en compte actuellement
            # The user is probably not allowed to do transfers
            return False
        elif code != "0":
            raise AssertionError("Unhandled code %s in transfer emitter selection" % code)

        for obj in Dict("content")(self.doc):

            for account in Dict("postes")(obj):
                _id = "{}.{}".format(
                    Dict("numero")(obj),
                    Dict("codeNature")(account),
                )
                if _id == account_id:
                    return True
        return False


class RecipientListPage(LoggedPage, JsonPage):
    @method
    class iter_external_recipients(DictElement):
        item_xpath = "content/listeComptesCExternes"
        # The id is the iban, and exceptionally there could be the same
        # recipient multiple times when the bic of the recipient changed
        ignore_duplicate = True

        class item(ItemElement):
            klass = Recipient

            obj_id = CleanText(Dict("id"))
            obj_iban = CleanText(Dict("iban"))
            obj_bank_name = CleanText(Dict("nomBanque"))
            obj_currency = Currency(Dict("monnaie/code"))
            obj_enabled_at = date.today()
            obj_label = CleanText(Dict("libelle"))
            obj_category = "Externe"

    @method
    class iter_internal_recipients(DictElement):
        def find_elements(self):
            for obj in Dict("content/listeComptesCInternes")(self):
                number = Dict("numero")(obj)
                for account in Dict("postes")(obj):
                    account["number"] = number
                    yield account

        class item(ItemElement):
            klass = Recipient

            obj_id = Format("%s.%s", Dict("number"), Dict("codeNature"))
            obj_label = CleanText(Dict("libelle"))
            obj_enabled_at = date.today()
            obj_currency = Currency(Dict("monnaie/code"))
            obj_bank_name = "BRED"
            obj_category = "Interne"


class ErrorJsonPage(JsonPage):
    def get_error_code(self):
        return CleanText(Dict("erreur/code"))(self.doc)

    def get_error(self):
        error = CleanText(Dict("erreur/libelle"))(self.doc)
        if error != "OK":
            # The message is some partial html, the useful message
            # is at the beginning, before every html tag so we just retrieve the
            # first part of the message before any html tag.
            # If the message begins with html tags, the regex will skip those.
            m = re.search(r"^(?:<[^>]+>)*(.+?)(?=<[^>]+>)", error)
            if m:
                return m.group(1)
            return error


class AddRecipientPage(LoggedPage, ErrorJsonPage):
    def get_transfer_limit(self):
        error = self.get_error()
        if not error:
            return None

        # The message is some partial html in a json key, we can't use
        # the html tags to limit the search.
        text_limit = Regexp(
            pattern=r"(?:plafond de virement est limité à|l'augmenter, au delà de) ([\d ,€]+)",
            default="",
        ).filter(error)

        return CleanDecimal.French(default=None).filter(text_limit)


class TransferPage(LoggedPage, ErrorJsonPage):
    def get_transfer_amount(self):
        return CleanDecimal(Dict("content/montant/valeur"))(self.doc)

    def get_transfer_currency(self):
        return Currency(Dict("content/montant/monnaie/code"))(self.doc)
