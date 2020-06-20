# -*- coding: utf-8 -*-

# Copyright(C) 2019 Sylvie Ye
#
# This file is part of a weboob module.
#
# This weboob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This weboob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this weboob module. If not, see <http://www.gnu.org/licenses/>.

# flake8: compatible

from __future__ import unicode_literals

from datetime import date

from weboob.browser.pages import (
    LoggedPage, JsonPage, RawPage, HTMLPage,
    PartialHTMLPage,
)
from weboob.browser.elements import method, ItemElement, DictElement
from weboob.capabilities.base import empty, NotAvailable
from weboob.capabilities.bank import (
    Account, Recipient, Transfer, TransferBankError, Emitter, EmitterNumberType,
)
from weboob.browser.filters.standard import (
    CleanDecimal, Date, CleanText, Coalesce, Format,
)
from weboob.browser.filters.json import Dict


class NewRecipientPage(LoggedPage, PartialHTMLPage):
    def get_error(self):
        return CleanText('//h2[strong[contains(text(), "pas pu être validé")]]/following-sibling::div/p')(self.doc)


class NewRecipientSmsPage(LoggedPage, HTMLPage):
    pass


class VerifyNewRecipientPage(LoggedPage, JsonPage):
    pass


class EndNewRecipientPage(LoggedPage, HTMLPage):
    def get_error(self):
        return CleanText('//div[contains(@class, "Beneficiary-main")]/h3/strong')(self.doc)

    def get_validated_message(self):
        return CleanText('//p[contains(text(), "bien été ajouté à vos bénéficiaires")]')(self.doc)


class SendSmsPage(LoggedPage, RawPage):
    pass


class ValidateSmsPage(LoggedPage, RawPage):
    pass


class CheckSmsPage(LoggedPage, HTMLPage):
    pass


class RecipientTokenPage(LoggedPage, RawPage):
    def get_token(self):
        return self.text


class ValidateNewRecipientPage(LoggedPage, JsonPage):
    def get_redirect_url(self):
        return CleanText(Dict('page'))(self.doc)


class RecipientsPage(LoggedPage, JsonPage):
    def is_sender_account(self, account_id):
        for acc in self.doc:
            if acc.get('senderOfTransfert') and account_id == acc.get('accountNumber'):
                return True

    @method
    class iter_debit_accounts(DictElement):
        def store(self, obj):
            # can have accounts with same ID
            # filter it on `browser.py` to have 'index' (needed to do transfer)
            return obj

        class item(ItemElement):
            def condition(self):
                return Dict('accountNumber', default=None)(self)

            klass = Account

            obj_id = obj_number = Dict('accountNumber')
            obj_label = Coalesce(
                Dict('accountNatureLongLabel', default=''),
                Dict('accountNatureShortLabel', default=''),
            )
            obj_iban = Dict('ibanCode')
            obj_currency = Dict('currencyCode')

            def obj_balance(self):
                balance_value = CleanDecimal(Dict('balanceValue'))(self)
                if CleanText(Dict('balanceSign'))(self) == '-':
                    return -balance_value
                return balance_value

    @method
    class iter_internal_recipient(DictElement):
        def store(self, obj):
            return obj

        class item(ItemElement):
            def condition(self):
                return Dict('accountNumber', default=None)(self)

            klass = Recipient

            obj_id = Dict('accountNumber')
            obj_label = CleanText(
                Format(
                    '%s %s',
                    Dict('accountHolderLongDesignation'),
                    Dict('accountNatureShortLabel', default=''),
                )
            )
            obj_iban = Dict('ibanCode')
            obj_category = 'Interne'
            obj_enabled_at = date.today()
            obj__is_recipient = Dict('recipientOfTransfert', default=False)
            obj__owner_name = CleanText(Dict('accountHolderLongDesignation'))

    @method
    class iter_external_recipient(DictElement):
        def store(self, obj):
            return obj

        class item(ItemElement):
            def condition(self):
                return Dict('recipientId', default=None)(self)

            klass = Recipient

            obj_id = obj_iban = Dict('ibanCode')
            obj_label = CleanText(Dict('recipientName'))
            obj_category = 'Externe'
            obj_enabled_at = date.today()

    @method
    class iter_emitters(DictElement):
        class item(ItemElement):
            def condition(self):
                return Dict('senderOfTransfert', default=None)(self) and Dict('accountNumber', default=None)(self)

            klass = Emitter

            obj_id = Dict('accountNumber')
            obj_number_type = EmitterNumberType.IBAN
            obj_number = Dict('ibanCode')
            obj_currency = Dict('currencyCode')

            def obj_label(self):
                """
                The label fields only contain the account type name. If we have several accounts
                of the same type, the labels will be the same (such as 3 accounts with the label 'Livret A').
                Add the owner name to differentiate them.
                """
                account_name = Coalesce(
                    CleanText(Dict('accountNatureLongLabel', default='')),
                    CleanText(Dict('accountNatureShortLabel', default='')),
                )
                owner_name = Coalesce(
                    CleanText(Dict('accountHolderLongDesignation', default='')),
                    CleanText(Dict('accountHolderShortDesignation', default='')),
                )
                return Format('%s %s', account_name, owner_name)(self)

            def obj_balance(self):
                # For some reason, the balance here is given without its decimal part
                balance_value = CleanDecimal(Dict('balanceValue', default=NotAvailable), default=NotAvailable)(self)

                if empty(balance_value):
                    return balance_value

                balance_value = balance_value / 100
                if CleanText(Dict('balanceSign'))(self) == '-':
                    return -balance_value
                return balance_value


class TransferTokenPage(LoggedPage, RawPage):
    def get_token(self):
        return self.text


class TransferPage(LoggedPage, JsonPage):
    def check_transfer(self):
        error_msg = Dict('messageErreur')(self.doc)
        if error_msg:
            raise TransferBankError(message=error_msg)
        return Dict('page')(self.doc) == '/recap'

    def handle_response(self, transfer):
        t = Transfer()
        t._space = transfer._space
        t._operation = transfer._operation
        t._token = transfer._token
        t._connection_id = transfer._connection_id

        t.label = Dict('transferComplementaryInformations1')(self.doc)
        t.exec_date = Date(Dict('dateVirement'), dayfirst=True)(self.doc)
        t.amount = CleanDecimal(Dict('amount'))(self.doc)
        t.currency = Dict('currencyCode')(self.doc)

        t.account_id = Dict('currentDebitAccountNumber')(self.doc)
        t.account_iban = Dict('currentDebitIbanCode')(self.doc)
        t.account_label = Dict('currentDebitTypeCompte')(self.doc)

        t.recipient_label = CleanText(Dict('currentCreditAccountName'))(self.doc)
        t.recipient_id = t.recipient_iban = Dict('currentCreditIbanCode')(self.doc)

        # Internal transfer
        if not Dict('isExternalTransfer')(self.doc):
            t.recipient_id = Dict('currentCreditAccountNumber')(self.doc)

        return t

    def check_transfer_exec(self):
        error_msg = Dict('messageErreur')(self.doc)
        if error_msg:
            raise TransferBankError(message=error_msg)
        return Dict('page')(self.doc)
