# -*- coding: utf-8 -*-

# Copyright(C) 2018      Sylvie Ye
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


import json
from datetime import date

from weboob.browser.pages import LoggedPage, HTMLPage, JsonPage
from weboob.browser.elements import method, DictElement, ItemElement, ListElement
from weboob.browser.filters.standard import CleanText, CleanDecimal
from weboob.browser.filters.html import Attr
from weboob.browser.filters.json import Dict
from weboob.browser.filters.standard import Date, Eval, Field
from weboob.capabilities.bank import Recipient, Transfer, Emitter, EmitterNumberType

from .pages import LoginEntPage
from ..pages.accounts_list import eval_decimal_amount


class ErrorCheckedJsonPage(JsonPage):
    def on_load(self):
        assert Dict('commun/statut')(self.doc) == 'ok', \
            'Something went wrong: %s' % Dict('commun/raison')(self.doc)


class RecipientsJsonPage(LoggedPage, ErrorCheckedJsonPage):
    def is_external_recipients(self):
        return Dict('donnees/items')(self.doc)

    def is_all_external_recipient(self):
        return (
            Dict('donnees/nbTotalDestinataires')(self.doc) == len(self.doc['donnees']['items'])
            and not Dict('donnees/moreItems')(self.doc)
        )

    @method
    class iter_external_recipients(DictElement):
        item_xpath = 'donnees/items'

        class item(ItemElement):
            klass = Recipient

            def condition(self):
                return Dict('coordonnee/0/natureTypee')(self) == 'CREDIT'

            obj_category = 'Externe'
            obj_id = Dict('coordonnee/0/refSICoordonnee')
            obj_iban = Dict('coordonnee/0/numeroCompte')
            obj_label = obj__account_title = Dict('nomRaisonSociale')
            obj_enabled_at = date.today()

            obj__formatted_iban = Dict('coordonnee/0/numeroCompteFormate')
            obj__bic = Dict('coordonnee/0/BIC')
            obj__ref = Dict('coordonnee/0/refSICoordonnee')
            obj__code_origin = Dict('coordonnee/0/codeOrigine')
            obj__created_date = Dict('dateCreationDest')


class TransferDatesPage(LoggedPage, ErrorCheckedJsonPage):
    def is_date_valid(self, exec_date):
        transfer_dates_list = Dict('donnees/listeDatesExecution')(self.doc)
        assert transfer_dates_list
        return exec_date.strftime('%d/%m/%Y') in transfer_dates_list


class EasyTransferPage(LoggedPage, HTMLPage):
    def update_origin_account(self, origin_account):
        for account in self.doc.xpath('//ul[@id="idCptFrom"]//li'):
            # get all account data
            data = Attr('.', 'data-comptecomplet')(account)
            json_data = json.loads(data.replace('&quot;', '"'))

            if (
                origin_account.label == CleanText().filter(json_data['libelleCompte'])
                and origin_account.iban == json_data['ibanCompte']
            ):
                origin_account._currency_code = json_data['codeDevise']
                origin_account._formatted_iban = json_data['ibanFormateCompte']
                origin_account._min_amount = json_data['montantMin']
                origin_account._max_amount = json_data['montantMax']
                origin_account._decimal_code = json_data['codeDecimal']
                origin_account._manage_counter = json_data['guichetGestionnaire']
                origin_account._account_title = json_data['intituleCompte']
                origin_account._bic = json_data['bicCompte']
                origin_account._id_service = json_data['idPrestation']
                origin_account._product_code = json_data['codeProduit']
                origin_account._underproduct_code = json_data['codeSousProduit']
                break
        else:
            # some accounts are not able to do transfer
            self.logger.warning('Account %s not found on transfer page', origin_account.label)

    def iter_internal_recipients(self):
        if self.doc.xpath('//ul[@id="idCmptToInterne"]'):
            for account in self.doc.xpath('//ul[@id="idCmptToInterne"]/li'):
                data = Attr('.', 'data-comptecomplet')(account)
                json_data = json.loads(data.replace('&quot;', '"'))

                rcpt = Recipient()
                rcpt.category = 'Interne'
                rcpt.id = rcpt.iban = json_data['ibanCompte']
                rcpt.label = json_data['libelleCompte']
                rcpt.enabled_at = date.today()

                rcpt._formatted_iban = json_data['ibanFormateCompte']
                rcpt._account_title = json_data['intituleCompte']
                rcpt._bic = json_data['bicCompte']
                rcpt._ref = ''
                rcpt._code_origin = ''
                rcpt._created_date = ''

                yield rcpt

    @method
    class iter_emitters(ListElement):
        item_xpath = '//ul[@id="idCptFrom"]//li'

        class Item(ItemElement):
            klass = Emitter

            @property
            def data(self):
                data = Attr('.', 'data-comptecomplet')(self)
                return json.loads(data.replace('&quot;', '"'))

            obj_number_type = EmitterNumberType.IBAN

            def obj_id(self):
                """
                Get the emitter ID from the IBAN the same way its done for Account
                """
                return Field('number')(self)[4:-2]

            def obj_label(self):
                return CleanText(Dict('libelleCompte'))(self.data)

            def obj_currency(self):
                 return Dict('devise')(self.data)

            def obj_balance(self):
                return eval_decimal_amount(
                    'soldeComptableVeille/valeurMontant',
                    'soldeComptableVeille/codeDecimalisation'
                )(self.data)

            def obj_number(self):
                 return Dict('ibanCompte')(self.data)


class TransferPage(LoggedPage, ErrorCheckedJsonPage):
    def handle_response(self, origin, recipient, amount, reason, exec_date):
        account_data = Dict('donnees/detailOrdre/compteEmetteur')(self.doc)
        recipient_data = Dict('donnees/listOperations/0/compteBeneficiaire')(self.doc)
        transfer_data = Dict('donnees/detailOrdre')(self.doc)

        transfer = Transfer()
        transfer._b64_id_transfer = Dict('idOrdre')(transfer_data)

        transfer.account_id = origin.id
        transfer.account_label = Dict('libelleCompte')(account_data)
        transfer.account_iban = Dict('ibanCompte')(account_data)
        transfer.account_balance = origin.balance

        transfer.recipient_id = recipient.id
        transfer.recipient_label = Dict('libelleCompte')(recipient_data)
        transfer.recipient_iban = Dict('ibanCompte')(recipient_data)

        transfer.currency = Dict('montantTotalOrdre/codeDevise')(transfer_data)
        transfer.amount = CleanDecimal(Eval(
            lambda x, y: x * (10 ** -y),
            Dict('montantTotalOrdre/valeurMontant'),
            Dict('montantTotalOrdre/codeDecimalisation')
        ))(transfer_data)
        transfer.exec_date = Date(Dict('dateExecution'), dayfirst=True)(transfer_data)
        transfer.label = Dict('libelleClientOrdre')(transfer_data)

        return transfer

    def is_transfer_validated(self):
        return Dict('donnees/statutOrdre')(self.doc) not in ('rejete', 'a_signer', )


class SignTransferPage(LoggedPage, LoginEntPage):
    def get_confirm_transfer_data(self, password):
        keyboard_data = self.get_keyboard_data()
        return {
            'codsec': keyboard_data['img'].get_codes(password[:6]),
            'cryptocvcs': keyboard_data['infos']['crypto'],
            'vk_op': 'sign',
        }


class AddRecipientPage(LoggedPage, HTMLPage):
    def get_countries(self):
        countries = {}
        for country in self.doc.xpath('//div[@id="div-pays-tiers"]//li[not(@data-codepays="")]'):
            countries.update({
                Attr('.', 'data-codepays')(country): Attr('.', 'data-libellepays')(country)
            })
        return countries


class AddRecipientStepPage(LoggedPage, ErrorCheckedJsonPage):
    def get_response_data(self):
        return self.doc['donnees']


class ConfirmRecipientPage(LoggedPage, ErrorCheckedJsonPage):
    def rcpt_after_sms(self, recipient):
        rcpt_data = self.doc['donnees']

        assert recipient.label == Dict('nomRaisonSociale')(rcpt_data)
        assert recipient.iban == Dict('coordonnee/0/numeroCompte')(rcpt_data)

        rcpt = Recipient()
        rcpt.id = Dict('coordonnee/0/refSICoordonnee')(rcpt_data)
        rcpt.iban = Dict('coordonnee/0/numeroCompte')(rcpt_data)
        rcpt.label = Dict('nomRaisonSociale')(rcpt_data)
        rcpt.category = u'Externe'
        rcpt.enabled_at = date.today()
        return rcpt
