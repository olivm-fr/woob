# -*- coding: utf-8 -*-

# Copyright(C) 2016 Baptiste Delpey
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

# flake8: compatible

from __future__ import unicode_literals

from datetime import datetime
import re

from weboob.browser.pages import LoggedPage, JsonPage, FormNotFound
from weboob.browser.elements import method, ItemElement, DictElement
from weboob.capabilities.bank import (
    Recipient, Transfer, TransferBankError, AddRecipientBankError, AddRecipientTimeout,
    Emitter, EmitterNumberType,
)
from weboob.tools.capabilities.bank.iban import is_iban_valid
from weboob.capabilities.base import NotAvailable
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Env, Date, Field, Format,
)
from weboob.browser.filters.html import Link, ReplaceEntities
from weboob.browser.filters.json import Dict
from weboob.tools.json import json
from weboob.exceptions import BrowserUnavailable, ActionNeeded

from .base import BasePage
from .login import MainPage
from .accounts_list import eval_decimal_amount


class TransferJson(LoggedPage, JsonPage):
    @property
    def logged(self):
        return Dict('commun/raison', default=None)(self.doc) != "niv_auth_insuff"

    def on_load(self):
        if Dict('commun/statut')(self.doc).upper() == 'NOK':
            if self.doc['commun'].get('action'):
                raise TransferBankError(message=ReplaceEntities(Dict('commun/action'))(self.doc))
            elif self.doc['commun'].get('raison') in ('err_tech', 'err_is'):
                # on SG website, there is unavalaible message 'Le service est momentanément indisponible.'
                raise BrowserUnavailable()
            elif self.doc['commun'].get('raison') == "niv_auth_insuff":
                return
            else:
                raise AssertionError(
                    'Something went wrong, transfer is not created: %s'
                    % self.doc['commun'].get('raison')
                )

    def get_acc_transfer_id(self, account):
        for acc in self.doc['donnees']['listeEmetteursBeneficiaires']['listeDetailEmetteurs']:
            if (
                account.id == Format('%s%s', Dict('codeGuichet'), Dict('numeroCompte'))(acc)
                or account.id == Dict('identifiantPrestation', default=NotAvailable)(acc)
            ):
                # return json_id to do transfer
                return acc['id']
        return False

    def is_able_to_transfer(self, account):
        return self.get_acc_transfer_id(account)

    def get_first_available_transfer_date(self):
        return Date(Dict('donnees/listeEmetteursBeneficiaires/premiereDateExecutionPossible'), dayfirst=True)(self.doc)

    def get_account_ibans_dict(self):
        account_ibans = {}
        for account in Dict('donnees/listeEmetteursBeneficiaires/listeDetailEmetteurs')(self.doc):
            account_ibans[Dict('identifiantPrestation')(account)] = Dict('iban')(account)
        return account_ibans

    @method
    class iter_recipients(DictElement):
        item_xpath = 'donnees/listeEmetteursBeneficiaires/listeDetailBeneficiaires'
        # Some recipients can be internal and external
        ignore_duplicate = True

        class Item(ItemElement):
            klass = Recipient

            # Assume all recipients currency is euros.
            obj_currency = u'EUR'
            obj_iban = Dict('iban')
            obj_label = Dict('libelleToDisplay')
            obj_enabled_at = datetime.now().replace(microsecond=0)

            # needed for transfer
            obj__json_id = Dict('id')

            def obj_category(self):
                if Dict('groupeRoleToDisplay')(self) == 'Comptes personnels':
                    return u'Interne'
                return u'Externe'

            # for retrocompatibility
            def obj_id(self):
                if Field('category')(self) == 'Interne':
                    return Format('%s%s', Dict('codeGuichet'), Dict('numeroCompte'))(self)
                return Dict('iban')(self)

            def condition(self):
                return Field('id')(self) != Env('account_id')(self) and is_iban_valid(Field('iban')(self))

            def validate(self, obj):
                return obj.label  # some recipients have an empty label

    def init_transfer(self, account, recipient, transfer):
        assert self.is_able_to_transfer(account), 'Account %s seems to be not able to do transfer' % account.id

        # SCT : standard transfer
        data = [
            ('an200_montant', transfer.amount),
            ('an200_typeVirement', 'SCT'),
            ('b64e200_idCompteBeneficiaire', recipient._json_id),
            ('b64e200_idCompteEmetteur', self.get_acc_transfer_id(account)),
            ('cl200_devise', u'EUR'),
            ('cl200_nomBeneficiaire', recipient.label),
            ('cl500_motif', transfer.label),
            ('dt10_dateExecution', transfer.exec_date.strftime('%d/%m/%Y')),
        ]

        headers = {'Referer': self.browser.absurl('/com/icd-web/vupri/virement.html')}
        self.browser.location(self.browser.absurl('/icd/vupri/data/vupri-check.json'), headers=headers, data=data)

    def handle_response(self, recipient):
        json_response = self.doc['donnees']

        transfer = Transfer()
        transfer.id = json_response['idVirement']
        transfer.label = json_response['motif']
        transfer.amount = CleanDecimal.French((CleanText(Dict('montantToDisplay'))))(json_response)
        transfer.currency = json_response['devise']
        transfer.exec_date = Date(Dict('dateExecution'), dayfirst=True)(json_response)

        transfer.account_id = Format('%s%s', Dict('codeGuichet'), Dict('numeroCompte'))(json_response['compteEmetteur'])
        transfer.account_iban = json_response['compteEmetteur']['iban']
        transfer.account_label = json_response['compteEmetteur']['libelleToDisplay']

        assert recipient._json_id == json_response['compteBeneficiaire']['id']
        transfer.recipient_id = recipient.id
        transfer.recipient_iban = json_response['compteBeneficiaire']['iban']
        transfer.recipient_label = json_response['compteBeneficiaire']['libelleToDisplay']

        return transfer

    def is_transfer_validated(self):
        return Dict('commun/statut')(self.doc).upper() == 'OK'

    @method
    class iter_emitters(DictElement):
        item_xpath = 'donnees/listeEmetteursBeneficiaires/listeDetailEmetteurs'

        class Item(ItemElement):
            klass = Emitter

            obj_id = Dict('numeroCompte')
            obj_label = Dict('libelleToDisplay')
            obj_currency = Dict('montantSoldeVeille/codeDevise')
            obj_balance = eval_decimal_amount(
                'montantSoldeVeille/valeurMontant',
                'montantSoldeVeille/codeDecimalisation'
            )
            obj_number_type = EmitterNumberType.IBAN
            obj_number = Dict('iban')


class SignTransferPage(LoggedPage, MainPage):
    def get_token(self):
        result_page = json.loads(self.content)
        assert result_page['commun']['statut'].upper() == 'OK', 'Something went wrong: %s' % result_page['commun']['raison']
        return result_page['donnees']['jeton']

    def get_confirm_transfer_data(self, password):
        token = self.get_token()
        keyboard_data = self.get_keyboard_data()

        pwd = keyboard_data['img'].get_codes(password[:6])
        t = pwd.split(',')
        newpwd = ','.join(t[self.strange_map[j]] for j in range(6))

        return {
            'codsec': newpwd,
            'cryptocvcs': keyboard_data['infos']['crypto'].encode('iso-8859-1'),
            'vkm_op': 'sign',
            'cl1000_jtn': token,
        }


class SignRecipientPage(LoggedPage, JsonPage):
    def on_load(self):
        assert Dict('commun/statut')(self.doc).upper() == 'OK', (
            'Something went wrong on sign recipient page: %s' % Dict('commun/raison')(self.doc)
        )

    def get_sign_method(self):
        if Dict('donnees/unavailibility_reason', default='')(self.doc) == 'oob_non_enrole':
            # message from the website
            raise AddRecipientBankError(message="Pour réaliser cette opération il est nécessaire d'utiliser le PASS SECURITE")
        return Dict('donnees/sign_proc')(self.doc).upper()

    def check_recipient_status(self):
        transaction_status = Dict('donnees/transaction_status')(self.doc)

        # check add new recipient status
        assert transaction_status in ('available', 'in_progress', 'aborted', 'rejected'), (
            'transaction_status is %s' % transaction_status
        )
        if transaction_status == 'aborted':
            raise AddRecipientTimeout()
        elif transaction_status == 'rejected':
            raise ActionNeeded("La demande d'ajout de bénéficiaire a été annulée.")
        elif transaction_status == 'in_progress':
            raise ActionNeeded('Veuillez valider le bénéficiaire sur votre application bancaire.')

    def get_transaction_id(self):
        return Dict('donnees/id-transaction')(self.doc)


class AddRecipientPage(LoggedPage, BasePage):
    def on_load(self):
        error_msg = CleanText('//span[@class="error_msg"]')(self.doc)
        if error_msg:
            if 'Le service est momentanément indisponible' in error_msg:
                # This has been seen on multiple connections. Whenever they tried
                # to add a recipient it failed with this message, but it worked
                # when they tried to do it the next day.
                raise BrowserUnavailable(error_msg)
            raise AddRecipientBankError(message=error_msg)

    def is_here(self):
        return (
            bool(CleanText('//h3[contains(text(), "Ajouter un compte bénéficiaire de virement")]')(self.doc))
            or bool(CleanText('//h1[contains(text(), "Ajouter un compte bénéficiaire de virement")]')(self.doc))
            or bool(
                CleanText('//h3[contains(text(), "Veuillez vérifier les informations du compte à ajouter")]')(self.doc)
            )
            or bool(CleanText('//span[contains(text(), "Le service est momentanément indisponible")]')(self.doc))
            or bool(Link('//a[contains(@href, "per_cptBen_ajouter")]', default=NotAvailable)(self.doc))
        )

    def post_iban(self, recipient):
        form = self.get_form(name='persoAjoutCompteBeneficiaire')
        form['codeIBAN'] = recipient.iban
        form['n10_form_etr'] = '1'
        form.submit()

    def post_label(self, recipient):
        form = self.get_form(name='persoAjoutCompteBeneficiaire')
        form['nomBeneficiaire'] = recipient.label
        form['codeIBAN'] = form['codeIBAN'].replace(' ', '')
        form['n10_form_etr'] = '1'
        form.submit()

    def get_action_level(self):
        for script in self.doc.xpath('//script'):
            if 'actionLevel' in CleanText('.')(script):
                return re.search(r"'actionLevel': (\d{3}),", script.text).group(1)

    def get_signinfo_data_form(self):
        try:
            form = self.get_form(id='formCache')
        except FormNotFound:
            raise AssertionError('Transfer auth form not found')
        return form

    def update_browser_recipient_state(self):
        form = self.get_signinfo_data_form()
        # set browser variable used to continue new recipient
        self.browser.context = form['context']
        self.browser.dup = form['dup']
        self.browser.logged = 1

    def get_signinfo_data(self):
        form = self.get_signinfo_data_form()
        signinfo_data = {}
        signinfo_data['b64_jeton_transaction'] = form['context']
        signinfo_data['action_level'] = self.get_action_level()
        return signinfo_data

    def get_recipient_object(self, recipient, get_info=False):
        r = Recipient()

        if get_info:
            recap_iban = CleanText(
                '//div[div[contains(text(), "IBAN")]]/div[has-class("recapTextField")]',
                replace=[(' ', '')]
            )(self.doc)
            assert recap_iban == recipient.iban

            recipient.bank_name = CleanText(
                '//div[div[contains(text(), "Banque du")]]/div[has-class("recapTextField")]'
            )(self.doc)

        r.iban = recipient.iban
        r.id = recipient.iban
        r.label = recipient.label
        r.category = recipient.category
        # On societe generale recipients are immediatly available.
        r.enabled_at = datetime.now().replace(microsecond=0)
        r.currency = u'EUR'
        r.bank_name = recipient.bank_name
        return r
