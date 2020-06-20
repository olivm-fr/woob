# -*- coding: utf-8 -*-

# Copyright(C) 2012 Romain Bignon
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

from __future__ import division
from __future__ import unicode_literals

import re
from base64 import b64decode
from collections import OrderedDict
from PIL import Image, ImageFilter
from io import BytesIO
from decimal import Decimal
from datetime import datetime
from lxml import html

from weboob.browser.pages import (
    LoggedPage, HTMLPage, JsonPage, pagination,
    FormNotFound, RawPage, XMLPage,
)
from weboob.browser.elements import ItemElement, method, ListElement, TableElement, SkipItem, DictElement
from weboob.browser.filters.standard import (
    Date, CleanDecimal, Regexp, CleanText, Env, Upper,
    Field, Eval, Format, Currency, Coalesce,
)
from weboob.browser.filters.html import Link, Attr, TableCell
from weboob.capabilities import NotAvailable
from weboob.capabilities.bank import (
    Account, Loan, AccountOwnership,
    Transfer, TransferBankError, TransferInvalidOTP,
    Recipient, AddRecipientBankError, RecipientInvalidOTP,
    Emitter, EmitterNumberType, AddRecipientError,
)
from weboob.capabilities.wealth import Investment
from weboob.capabilities.bill import DocumentTypes, Subscription, Document
from weboob.tools.capabilities.bank.investments import is_isin_valid
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.capabilities.bank.iban import is_rib_valid, rib2iban, is_iban_valid
from weboob.tools.captcha.virtkeyboard import SplitKeyboard, GridVirtKeyboard
from weboob.tools.compat import unicode
from weboob.exceptions import (
    NoAccountsException, BrowserUnavailable, ActionNeeded, BrowserIncorrectPassword,
    BrowserPasswordExpired,
)
from weboob.browser.filters.json import Dict
from weboob.browser.exceptions import ClientError

from .base_pages import fix_form, BasePage


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True)
    return CleanDecimal(*args, **kwargs)


class MyTableCell(TableCell):
    def __init__(self, *names, **kwargs):
        super(MyTableCell, self).__init__(*names, **kwargs)
        self.td = './tr[%s]/td'


def float_to_decimal(f):
    return Decimal(str(f))


class NewLoginPage(HTMLPage):
    def get_main_js_file_url(self):
        return Attr('//script[contains(@src, "main-")]', 'src')(self.doc)


class LoginPage(JsonPage):
    def on_load(self):
        error_msg = self.doc.get('error')
        if error_msg and 'Le service est momentanément indisponible' in error_msg:
            raise BrowserUnavailable(error_msg)

    def get_response(self):
        return self.doc

    def get_wrongpass_message(self):
        error_msg = Dict('error')(self.doc)
        if (
            "Nous n'avons pas réussi à vous authentifier" in error_msg or
            'abonnement est bloqué' in error_msg
        ):
            return error_msg
        assert False, 'Other error message to catch on LoginPage'


class JsFilePage(RawPage):
    def get_client_id(self):
        return Regexp(pattern=r'{authenticated:{clientId:"([^"]+)"').filter(self.text)

    def get_nonce(self):
        return Regexp(pattern=r'\("nonce","([a-z0-9]+)"\)').filter(self.text)


class AuthorizePage(HTMLPage):
    def send_form(self):
        form = self.get_form(id='submitMe')
        form.submit()


class AuthenticationMethodPage(JsonPage):
    def get_validation_id(self):
        return Dict('id')(self.doc)

    @property
    def validation_units(self):
        units = Coalesce(
            Dict('step/validationUnits', default=None),
            Dict('validationUnits', default=None),
        )(self.doc)
        return units[0]

    @property
    def validation_unit_id(self):
        assert len(self.validation_units) == 1
        # The data we are looking for is in a dict with a random uuid key.
        return next(iter(self.validation_units))

    def get_authentication_method_info(self):
        # The data we are looking for is in a dict with a random uuid key.
        return self.validation_units[self.validation_unit_id][0]

    def get_authentication_method_type(self):
        return self.get_authentication_method_info()['type']

    def login_errors(self, error):
        # AUTHENTICATION_LOCKED is a BrowserIncorrectPassword because there is a key
        # 'unlockingDate', in the json, that tells when the account will be unlocked.
        # So it does not require any action from the user and is automatic.
        if error in ('FAILED_AUTHENTICATION', 'AUTHENTICATION_LOCKED', 'AUTHENTICATION_FAILED'):
            raise BrowserIncorrectPassword()
        if error in ('ENROLLMENT', ):
            raise BrowserPasswordExpired()

    def transfer_errors(self, error):
        if error == 'FAILED_AUTHENTICATION':
            # For the moment, only otp sms is handled
            raise TransferInvalidOTP(message="Le code SMS que vous avez renseigné n'est pas valide")

    def recipient_errors(self, error):
        if error == 'FAILED_AUTHENTICATION':
            # For the moment, only otp sms is handled
            raise RecipientInvalidOTP(message="Le code SMS que vous avez renseigné n'est pas valide")
        elif error == 'AUTHENTICATION_CANCELED':
            raise AddRecipientError(message="L'ajout a été annulée via l'application mobile.")

    def check_errors(self, feature):
        if 'response' in self.doc:
            result = self.doc['response']['status']
        elif 'step' in self.doc:
            # Can have error at first authentication request,
            # error will be handle in `if` case.
            # If there is no error, it will retrive 'AUTHENTICATION' as result value.
            result = self.doc['step']['phase']['state']
        elif 'phase' in self.doc and self.get_authentication_method_type() == 'PASSWORD_ENROLL':
            result = self.doc['phase']['state']
        else:
            result = self.doc['phase']['previousResult']

        if result in ('AUTHENTICATION', 'AUTHENTICATION_SUCCESS'):
            return

        FEATURES_ERRORS = {
            'login': self.login_errors,
            'transfer': self.transfer_errors,
            'recipient': self.recipient_errors,
        }
        FEATURES_ERRORS[feature](error=result)

        assert False, 'Error during %s authentication is not handled yet: %s' % (feature, result)


class AuthenticationStepPage(AuthenticationMethodPage):
    def get_redirect_data(self):
        return Dict('response/saml2_post')(self.doc)


class VkImagePage(JsonPage):
    def get_all_images_data(self):
        return self.doc


class ValidationPageOption(LoggedPage, HTMLPage):
    pass


class LoginTokensPage(JsonPage):
    def get_access_token(self):
        return Dict('parameters/access_token')(self.doc)

    def get_id_token(self):
        return Dict('parameters/id_token')(self.doc)


class CaissedepargneNewKeyboard(SplitKeyboard):
    char_to_hash = {
        '0': '66ec79b200706e7f9c14f2b6d35dbb05',
        '1': ('529819241cce382b429b4624cb019b56', '0ea8c08e52d992a28aa26043ffc7c044'),
        '2': 'fab68678204198b794ce580015c8637f',
        '3': '3fc5280d17cf057d1c4b58e4f442ceb8',
        '4': ('dea8800bdd5fcaee1903a2b097fbdef0', 'e413098a4d69a92d08ccae226cea9267', '61f720966ccac6c0f4035fec55f61fe6', '2cbd19a4b01c54b82483f0a7a61c88a1'),
        '5': 'ff1909c3b256e7ab9ed0d4805bdbc450',
        '6': '7b014507ffb92a80f7f0534a3af39eaa',
        '7': '7d598ff47a5607022cab932c6ad7bc5b',
        '8': ('4ed28045e63fa30550f7889a18cdbd81', '88944bdbef2e0a49be9e0c918dd4be64'),
        '9': 'dd6317eadb5a0c68f1938cec21b05ebe',
    }
    codesep = ' '

    def __init__(self, browser, images):
        code_to_filedata = {}
        for img_item in images:
            img_content = browser.location(img_item['uri']).content
            img = Image.open(BytesIO(img_content))
            img = img.filter(ImageFilter.UnsharpMask(
                radius=2,
                percent=150,
                threshold=3,
            ))
            img = img.convert('L', dither=None)
            img = Image.eval(img, lambda x: 0 if x < 20 else 255)
            b = BytesIO()
            img.save(b, format='PNG')
            code_to_filedata[img_item['value']] = b.getvalue()
        super(CaissedepargneNewKeyboard, self).__init__(code_to_filedata)


class CaissedepargneKeyboard(GridVirtKeyboard):
    color = (255, 255, 255)
    margin = 3, 3
    symbols = {
        '0': 'ef8d775a73b751c5fbee06e2d537785c',
        '1': 'bf51842846c3045f76355de32e4689c7',
        '2': 'e4c057317b7ceb17241a0ae4c26844c4',
        '3': 'c28c0c109a63f034d0f7c0f7ffdb364c',
        '4': '6ea6a5152efb1d12c33f9cbf9476caec',
        '5': '7ec4b424b5db7e7b2a54e6300fdb7515',
        '6': 'a1fa95fc856804f978f20ad42c60f6d7',
        '7': '64646adaa5a0b2506880970d8e928156',
        '8': '4abcc6b24fa77f3756b96257962615eb',
        '9': '3f41daf8ca5f250be5df91fe24079735',
    }

    def __init__(self, image, symbols):
        image = BytesIO(b64decode(image.encode('ascii')))
        super(CaissedepargneKeyboard, self).__init__(symbols, 5, 3, image, self.color, convert='RGB')

    def check_color(self, pixel):
        for c in pixel:
            if c < 250:
                return True


class GarbagePage(LoggedPage, HTMLPage):
    def on_load(self):
        go_back_link = Link('//a[@class="btn" or @class="cta_stroke back"]', default=NotAvailable)(self.doc)

        if go_back_link is not NotAvailable:
            assert len(go_back_link) != 1
            go_back_link = re.search('\(~deibaseurl\)(.*)$', go_back_link).group(1)

            self.browser.location('%s%s' % (self.browser.BASEURL, go_back_link))


class MessagePage(GarbagePage):
    def get_message(self):
        return CleanText('//form[contains(@name, "leForm")]//span')(self.doc)

    def submit(self):
        form = self.get_form(name='leForm')

        form['signatur1'] = ['on']

        form.submit()


class _LogoutPage(HTMLPage):
    def on_load(self):
        raise BrowserUnavailable(CleanText('//*[@class="messErreur"]')(self.doc))


class ErrorPage(_LogoutPage):
    pass


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^CB (?P<text>.*?) FACT (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})\b', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^RET(RAIT)? DAB (?P<dd>\d+)-(?P<mm>\d+)-.*', re.IGNORECASE), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^RET(RAIT)? DAB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) (?P<HH>\d{2})H(?P<MM>\d{2})\b', re.IGNORECASE), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^VIR(EMENT)?(\.PERIODIQUE)? (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^PRLV (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^CHEQUE.*', re.IGNORECASE), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(CONVENTION \d+ )?COTIS(ATION)? (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^\* ?(?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^REMISE (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^Depot Esp (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>.*)( \d+)? QUITTANCE .*', re.IGNORECASE), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^CB [\d\*]+ TOT DIF .*', re.IGNORECASE), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^CB [\d\*]+ (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^CB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})\b', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'\*CB (?P<text>.*?) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2})\b', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^FAC CB (?P<text>.*?) (?P<dd>\d{2})/(?P<mm>\d{2})\b', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^\*?CB (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_CARD),
        # For life insurances and capitalisation contracts
        (re.compile(r'^VERSEMENT', re.IGNORECASE), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^Réinvestissement', re.IGNORECASE), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^REVALORISATION', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^ARBITRAGE', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^RACHAT PARTIEL', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(?P<text>INTERETS.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
    ]


class IndexPage(LoggedPage, BasePage):
    ACCOUNT_TYPES = {
        'Epargne liquide': Account.TYPE_SAVINGS,
        'Compte Courant': Account.TYPE_CHECKING,
        'COMPTE A VUE': Account.TYPE_CHECKING,
        'COMPTE CHEQUE': Account.TYPE_CHECKING,
        'Mes comptes': Account.TYPE_CHECKING,
        'CPT DEPOT PART.': Account.TYPE_CHECKING,
        'CPT DEPOT PROF.': Account.TYPE_CHECKING,
        'Mon épargne': Account.TYPE_SAVINGS,
        'Mes autres comptes': Account.TYPE_SAVINGS,
        'Compte Epargne et DAT': Account.TYPE_SAVINGS,
        'Plan et Contrat d\'Epargne': Account.TYPE_SAVINGS,
        'COMPTE SUR LIVRET': Account.TYPE_SAVINGS,
        'LIVRET DEV.DURABLE': Account.TYPE_SAVINGS,
        'LDD Solidaire': Account.TYPE_SAVINGS,
        'LIVRET A': Account.TYPE_SAVINGS,
        'LIVRET JEUNE': Account.TYPE_SAVINGS,
        'LIVRET GRAND PRIX': Account.TYPE_SAVINGS,
        'LEP': Account.TYPE_SAVINGS,
        'L.EPAR POPULAIRE': Account.TYPE_SAVINGS,
        'LEL': Account.TYPE_SAVINGS,
        'PLAN EPARG. LOGEMENT': Account.TYPE_SAVINGS,
        'L. EPAR LOGEMENT': Account.TYPE_SAVINGS,
        'CPT PARTS SOCIALES': Account.TYPE_MARKET,
        'PEL': Account.TYPE_SAVINGS,
        'PEL 16 2013': Account.TYPE_SAVINGS,
        'PEL 16 2014': Account.TYPE_SAVINGS,
        'PARTS SOCIALES': Account.TYPE_MARKET,
        'Titres': Account.TYPE_MARKET,
        'Compte titres': Account.TYPE_MARKET,
        'Mes crédits immobiliers': Account.TYPE_LOAN,
        'Mes crédits renouvelables': Account.TYPE_LOAN,
        'Mes crédits consommation': Account.TYPE_LOAN,
        'PEA NUMERAIRE': Account.TYPE_PEA,
        'PEA': Account.TYPE_PEA,
    }

    def on_load(self):

        # For now, we have to handle this because after this warning message,
        # the user is disconnected (even if all others account are reachable)
        if 'OIC_QCF' in self.browser.url:
            # QCF is a mandatory test to make sure you know the basics about financials products
            # however, you can still choose to postpone it. hence the continue link
            link = Link('//span[@id="lea-prdvel-lien"]//b/a[contains(text(), "Continuer")]')(self.doc)
            if link:
                self.logger.warning("By-passing QCF")
                self.browser.location(link)
            else:
                message = CleanText(self.doc.xpath('//span[contains(@id, "OIC_QCF")]/p'))(self)
                if message and "investissement financier (QCF) n’est plus valide à ce jour ou que vous avez refusé d’y répondre" in message:
                    raise ActionNeeded(message)

        mess = CleanText('//body/div[@class="content"]//p[contains(text(), "indisponible pour cause de maintenance")]')(self.doc)
        if mess:
            raise BrowserUnavailable(mess)

        # This page is sometimes an useless step to the market website.
        bourse_link = Link('//div[@id="MM_COMPTE_TITRE_pnlbourseoic"]//a[contains(text(), "Accédez à la consultation")]', default=None)(self.doc)

        if bourse_link:
            self.browser.location(bourse_link)

    def need_auth(self):
        return bool(CleanText('//span[contains(text(), "Authentification non rejouable")]')(self.doc))

    def check_no_loans(self):
        return (
            not bool(CleanText('//table[@class="menu"]//div[contains(., "Crédits")]')(self.doc))
            and not bool(CleanText('//table[@class="header-navigation_main"]//a[contains(., "Crédits")]')(self.doc))
        )

    def check_measure_accounts(self):
        return not CleanText('//div[@class="MessageErreur"]/ul/li[contains(text(), "Aucun compte disponible")]')(self.doc)

    def check_no_accounts(self):
        no_account_message = CleanText('//span[@id="MM_LblMessagePopinError"]/p[contains(text(), "Aucun compte disponible")]')(self.doc)

        if no_account_message:
            raise NoAccountsException(no_account_message)

    def find_and_replace(self, info, acc_id):
        # The site might be broken: id in js: 4097800039137N418S00197, id in title: 1379418S001 (N instead of 9)
        # So we seek for a 1 letter difference and replace if found .... (so sad)
        for i in range(len(info['id']) - len(acc_id) + 1):
            sub_part = info['id'][i:i + len(acc_id)]
            z = zip(sub_part, acc_id)
            if len([tuple_letter for tuple_letter in z if len(set(tuple_letter)) > 1]) == 1:
                info['link'] = info['link'].replace(sub_part, acc_id)
                info['id'] = info['id'].replace(sub_part, acc_id)
                return

    def _get_account_info(self, a, accounts):
        m = re.search("PostBack(Options)?\([\"'][^\"']+[\"'],\s*['\"]([HISTORIQUE_\w|SYNTHESE_ASSURANCE_CNP|BOURSE|COMPTE_TITRE][\d\w&]+)?['\"]", a.attrib.get('href', ''))
        if m is None:
            return None
        else:
            # it is in form CB&12345[&2]. the last part is only for new website
            # and is necessary for navigation.
            link = m.group(2)
            parts = link.split('&')
            info = {}
            info['link'] = link
            id = re.search("([\d]+)", a.attrib.get('title', ''))
            if len(parts) > 1:
                info['type'] = parts[0]
                info['id'] = info['_id'] = parts[1]
                if id or info['id'] in [acc._info['_id'] for acc in accounts.values()]:
                    _id = id.group(1) if id else next(iter({k for k, v in accounts.items() if info['id'] == v._info['_id']}))
                    self.find_and_replace(info, _id)
            else:
                info['type'] = link
                info['id'] = info['_id'] = id.group(1)
            if info['type'] in ('SYNTHESE_ASSURANCE_CNP', 'SYNTHESE_EPARGNE', 'ASSURANCE_VIE'):
                info['acc_type'] = Account.TYPE_LIFE_INSURANCE
            if info['type'] in ('BOURSE', 'COMPTE_TITRE'):
                info['acc_type'] = Account.TYPE_MARKET
            return info

    def is_account_inactive(self, account_id):
        return self.doc.xpath('//tr[td[contains(text(), $id)]][@class="Inactive"]', id=account_id)

    def _add_account(self, accounts, link, label, account_type, balance, number=None, ownership=NotAvailable):
        info = self._get_account_info(link, accounts)
        if info is None:
            self.logger.warning('Unable to parse account %r: %r' % (label, link))
            return

        account = Account()
        account._card_links = None
        account.id = info['id']
        if is_rib_valid(info['id']):
            account.iban = rib2iban(info['id'])
        account._info = info
        account.number = number
        account.label = label
        account.ownership = ownership
        account.type = self.ACCOUNT_TYPES.get(label, info['acc_type'] if 'acc_type' in info else account_type)
        if 'PERP' in account.label:
            account.type = Account.TYPE_PERP
        if 'NUANCES CAPITALISATI' in account.label:
            account.type = Account.TYPE_CAPITALISATION
        if account.type in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_PERP):
            account.ownership = AccountOwnership.OWNER

        balance = balance or self.get_balance(account)

        account.balance = Decimal(FrenchTransaction.clean_amount(balance)) if balance and balance is not NotAvailable else NotAvailable

        account.currency = account.get_currency(balance) if balance and balance is not NotAvailable else NotAvailable
        account._card_links = []

        # Set coming history link to the parent account. At this point, we don't have card account yet.
        if account._info['type'] == 'HISTORIQUE_CB' and account.id in accounts:
            a = accounts[account.id]
            a.coming = Decimal('0.0')
            a._card_links = account._info
            return

        accounts[account.id] = account
        return account

    def get_balance(self, account):
        if account.type not in (Account.TYPE_LIFE_INSURANCE, Account.TYPE_PERP, Account.TYPE_CAPITALISATION):
            return NotAvailable
        page = self.go_history(account._info).page
        balance = page.doc.xpath('.//tr[td[contains(@id,"NumContrat")]]/td[@class="somme"]/a[contains(@href, $id)]', id=account.id)
        if len(balance) > 0:
            balance = CleanText('.')(balance[0])
            balance = balance if balance != '' else NotAvailable
        else:
            # Specific xpath for some Life Insurances:
            balance = page.doc.xpath('//tr[td[contains(text(), $id)]]/td/div[contains(@id, "Solde")]', id=account.id)
            if len(balance) > 0:
                balance = CleanText('.')(balance[0])
                balance = balance if balance != '' else NotAvailable
            else:
                # sometimes the accounts are attached but no info is available
                balance = NotAvailable
        self.go_list()
        return balance

    def get_measure_balance(self, account):
        for tr in self.doc.xpath('//table[@cellpadding="1"]/tr[not(@class)]'):
            account_number = CleanText('./td/a[contains(@class, "NumeroDeCompte")]')(tr)
            if re.search(r'[A-Z]*\d{3,}', account_number).group() in account.id:
                # The regex '\s\d{1,3}(?:[\s.,]\d{3})*(?:[\s.,]\d{2})' matches for example '106 100,64'
                return re.search(r'\s\d{1,3}(?:[\s.,]\d{3})*(?:[\s.,]\d{2})', account_number).group()
        return NotAvailable

    def get_measure_ids(self):
        accounts_id = []
        for a in self.doc.xpath('//table[@cellpadding="1"]/tr/td[2]/a'):
            accounts_id.append(re.search("(\d{6,})", Attr('.', 'href')(a)).group(1))
        return accounts_id

    def has_next_page(self):
        return self.doc.xpath('//div[@id="MM_SYNTHESE_MESURES_m_DivLinksPrecSuiv"]//a[contains(text(), "Page suivante")]')

    def goto_next_page(self):
        form = self.get_form(id="main")

        form['__EVENTTARGET'] = 'MM$SYNTHESE_MESURES$lnkSuivante'
        form['__EVENTARGUMENT'] = ''
        form['m_ScriptManager'] = 'MM$m_UpdatePanel|MM$SYNTHESE_MESURES$lnkSuivante'
        fix_form(form)
        form.submit()

    def get_list(self, owner_name):
        accounts = OrderedDict()

        # Old website
        self.browser.new_website = False
        for table in self.doc.xpath('//table[@cellpadding="1"]'):
            account_type = Account.TYPE_UNKNOWN
            for tr in table.xpath('./tr'):
                tds = tr.findall('td')
                if tr.attrib.get('class', '') == 'DataGridHeader':
                    account_type = (
                        self.ACCOUNT_TYPES.get(tds[1].text.strip())
                        or self.ACCOUNT_TYPES.get(CleanText('.')(tds[2]))
                        or self.ACCOUNT_TYPES.get(CleanText('.')(tds[3]), Account.TYPE_UNKNOWN)
                    )
                else:
                    # On the same row, there could have many accounts (check account and a card one).
                    # For the card line, the number will be the same than the checking account, so we skip it.
                    ownership = self.get_ownership(tds, owner_name)
                    if len(tds) > 4:
                        for i, a in enumerate(tds[2].xpath('./a')):
                            label = CleanText('.')(a)
                            balance = CleanText('.')(tds[-2].xpath('./a')[i])
                            number = None
                            # if i > 0, that mean it's a card account. The number will be the same than it's
                            # checking parent account, we have to skip it.
                            if i == 0:
                                number = CleanText('.')(tds[-4].xpath('./a')[0])
                            self._add_account(accounts, a, label, account_type, balance, number, ownership=ownership)
                    # Only 4 tds on "banque de la reunion" website.
                    elif len(tds) == 4:
                        for i, a in enumerate(tds[1].xpath('./a')):
                            label = CleanText('.')(a)
                            balance = CleanText('.')(tds[-1].xpath('./a')[i])
                            self._add_account(accounts, a, label, account_type, balance, ownership=ownership)

        self.logger.warning('we are on the %s website', 'old' if accounts else 'new')

        if len(accounts) == 0:
            # New website
            self.browser.new_website = True
            for table in self.doc.xpath('//div[@class="panel"]'):
                title = table.getprevious()
                if title is None:
                    continue
                account_type = self.ACCOUNT_TYPES.get(CleanText('.')(title), Account.TYPE_UNKNOWN)
                for tr in table.xpath('.//tr'):
                    tds = tr.findall('td')
                    for i in range(len(tds)):
                        a = tds[i].find('a')
                        if a is not None:
                            break

                    if a is None:
                        continue

                    # sometimes there's a tooltip span to ignore next to <strong>
                    # (perhaps only on creditcooperatif)
                    label = CleanText('./strong')(tds[0])
                    balance = CleanText('.')(tds[-1])
                    ownership = self.get_ownership(tds, owner_name)

                    account = self._add_account(accounts, a, label, account_type, balance, ownership=ownership)
                    if account:
                        account.number = CleanText('.')(tds[1])

        return list(accounts.values())

    def get_ownership(self, tds, owner_name):
        if len(tds) > 2:
            account_owner = CleanText('.', default=None)(tds[2]).upper()
            if account_owner and any(title in account_owner for title in ('M', 'MR', 'MLLE', 'MLE', 'MME')):
                if re.search(r'(m|mr|me|mme|mlle|mle|ml)\.? ?(.*)\bou (m|mr|me|mme|mlle|mle|ml)\b(.*)', account_owner, re.IGNORECASE):
                    return AccountOwnership.CO_OWNER
                elif all(n in account_owner for n in owner_name.split()):
                    return AccountOwnership.OWNER
                return AccountOwnership.ATTORNEY
        return NotAvailable

    def is_access_error(self):
        error_message = u"Vous n'êtes pas autorisé à accéder à cette fonction"
        if error_message in CleanText('//div[@class="MessageErreur"]')(self.doc):
            return True

        return False

    def go_loans_conso(self, tr):

        link = tr.xpath('./td/a[contains(@id, "IdaCreditPerm")]')
        m = re.search('CREDITCONSO&(\w+)', link[0].attrib['href'])
        if m:
            account = m.group(1)

        form = self.get_form(id="main")
        form['__EVENTTARGET'] = 'MM$SYNTHESE_CREDITS'
        form['__EVENTARGUMENT'] = 'ACTIVDESACT_CREDITCONSO&%s' % account
        form['m_ScriptManager'] = 'MM$m_UpdatePanel|MM$SYNTHESE_CREDITS'
        form.submit()

    def get_loan_list(self):
        accounts = OrderedDict()

        # Old website
        for tr in self.doc.xpath('//table[@cellpadding="1"]/tr[not(@class) and td[a]]'):
            tds = tr.findall('td')

            account = Account()
            account._card_links = None
            account.id = CleanText('./a')(tds[2]).split('-')[0].strip()
            account.label = CleanText('./a')(tds[2]).split('-')[-1].strip()
            account.type = Account.TYPE_LOAN
            account.balance = -CleanDecimal('./a', replace_dots=True)(tds[4])
            account.currency = account.get_currency(CleanText('./a')(tds[4]))
            accounts[account.id] = account

        self.logger.debug('we are on the %s website', 'old' if accounts else 'new')

        if len(accounts) == 0:
            # New website
            for table in self.doc.xpath('//div[@class="panel"]'):
                title = table.getprevious()
                if title is None:
                    continue
                if "immobiliers" not in CleanText('.')(title):
                    account_type = self.ACCOUNT_TYPES.get(CleanText('.')(title), Account.TYPE_UNKNOWN)
                    for tr in table.xpath('./table/tbody/tr[contains(@id,"MM_SYNTHESE_CREDITS") and contains(@id,"IdTrGlobal")]'):
                        tds = tr.findall('td')
                        if len(tds) == 0:
                            continue
                        for i in tds[0].xpath('.//a/strong'):
                            label = i.text.strip()
                            break
                        if len(tds) == 3 and Decimal(FrenchTransaction.clean_amount(CleanText('.')(tds[-2]))) and any(cls in Attr('.', 'id')(tr) for cls in ['dgImmo', 'dgConso']) is False:
                            # in case of Consumer credit or revolving credit, we substract avalaible amount with max amout
                            # to get what was spend
                            balance = Decimal(FrenchTransaction.clean_amount(CleanText('.')(tds[-2]))) - Decimal(FrenchTransaction.clean_amount(CleanText('.')(tds[-1])))
                        else:
                            balance = Decimal(FrenchTransaction.clean_amount(CleanText('.')(tds[-1])))
                        account = Loan()
                        account.id = label.split(' ')[-1]
                        account.label = unicode(label)
                        account.type = account_type
                        account.balance = -abs(balance)
                        account.currency = account.get_currency(CleanText('.')(tds[-1]))
                        account._card_links = []
                        # The website doesn't show any information relative to the loan
                        # owner, we can then assume they all belong to the credentials owner.
                        account.ownership = AccountOwnership.OWNER

                        if "renouvelables" in CleanText('.')(title):
                            if 'JSESSIONID' in self.browser.session.cookies:
                                # Need to delete this to access the consumer loans space (a new one will be created)
                                del self.browser.session.cookies['JSESSIONID']
                            try:
                                self.go_loans_conso(tr)
                            except ClientError as e:
                                if e.response.status_code == 401:
                                    raise ActionNeeded('La situation actuelle de votre dossier ne vous permet pas d\'accéder à cette fonctionnalité. '
                                        'Nous vous invitons à contacter votre Centre de relation Clientèle pour accéder à votre prêt.')
                                raise
                            d = self.browser.loans_conso()
                            if d:
                                account.total_amount = float_to_decimal(d['contrat']['creditMaxAutorise'])
                                account.available_amount = float_to_decimal(d['situationCredit']['disponible'])
                                account.next_payment_amount = float_to_decimal(d['situationCredit']['mensualiteEnCours'])
                        accounts[account.id] = account
        return list(accounts.values())

    @method
    class get_real_estate_loans(ListElement):
        # beware the html response is slightly different from what can be seen with the browser
        # because of some JS most likely: use the native HTML response to build the xpath
        item_xpath = '//h3[contains(text(), "immobiliers")]//following-sibling::div[@class="panel"][1]//div[@id[starts-with(.,"MM_SYNTHESE_CREDITS")] and contains(@id, "IdDivDetail")]'

        class iter_account(TableElement):
            item_xpath = './table[@class="static"][1]/tbody'
            head_xpath = './table[@class="static"][1]/tbody/tr/th'

            col_total_amount = 'Capital Emprunté'
            col_rate = 'Taux d’intérêt nominal'
            col_balance = 'Capital Restant Dû'
            col_last_payment_date = 'Dernière échéance'
            col_next_payment_amount = 'Montant prochaine échéance'
            col_next_payment_date = 'Prochaine échéance'

            def parse(self, el):
                self.env['id'] = CleanText("./h2")(el).split()[-1]
                self.env['label'] = CleanText("./h2")(el)

            class item(ItemElement):

                klass = Loan

                obj_id = Env('id')
                obj_label = Env('label')
                obj_type = Loan.TYPE_LOAN
                obj_total_amount = MyDecimal(MyTableCell("total_amount"))
                obj_balance = MyDecimal(MyTableCell("balance"), sign=lambda x: -1)
                obj_currency = Currency(MyTableCell("balance"))
                obj_last_payment_date = Date(CleanText(MyTableCell("last_payment_date")))
                obj_next_payment_amount = MyDecimal(MyTableCell("next_payment_amount"))
                obj_next_payment_date = Date(CleanText(MyTableCell("next_payment_date", default=''), default=NotAvailable), default=NotAvailable)
                obj_rate = MyDecimal(MyTableCell("rate", default=NotAvailable), default=NotAvailable)
                # The website doesn't show any information relative to the loan
                # owner, we can then assume they all belong to the credentials owner.
                obj_ownership = AccountOwnership.OWNER

    def submit_form(self, form, eventargument, eventtarget, scriptmanager):
        form['__EVENTARGUMENT'] = eventargument
        form['__EVENTTARGET'] = eventtarget
        form['m_ScriptManager'] = scriptmanager
        fix_form(form)
        form.submit()

    def go_levies(self, account_id=None):
        form = self.get_form(id='main')
        if account_id:
            # Go to an account specific levies page
            eventargument = ""
            if "MM$m_CH$IsMsgInit" in form:
                # Old website
                form['MM$SYNTHESE_SDD_RECUS$m_ExDropDownList'] = account_id
                eventtarget = "MM$SYNTHESE_SDD_RECUS$m_ExDropDownList"
                scriptmanager = "MM$m_UpdatePanel|MM$SYNTHESE_SDD_RECUS$m_ExDropDownList"
            else:
                # New website
                form['MM$SYNTHESE_SDD_RECUS$ddlCompte'] = account_id
                eventtarget = "MM$SYNTHESE_SDD_RECUS$ddlCompte"
                scriptmanager = "MM$m_UpdatePanel|MM$SYNTHESE_SDD_RECUS$ddlCompte"
            self.submit_form(form, eventargument, eventtarget, scriptmanager,)
        else:
            # Go to an general levies page page where all levies are found
            if "MM$m_CH$IsMsgInit" in form:
                # Old website
                eventargument = "SDDRSYN0"
                eventtarget = "Menu_AJAX"
                scriptmanager = "m_ScriptManager|Menu_AJAX"
            else:
                # New website
                eventargument = "SDDRSYN0&codeMenu=WPS1"
                eventtarget = "MM$Menu_Ajax"
                scriptmanager = "MM$m_UpdatePanel|MM$Menu_Ajax"
            self.submit_form(form, eventargument, eventtarget, scriptmanager,)

    def go_list(self):

        form = self.get_form(id='main')
        eventargument = "CPTSYNT0"

        if "MM$m_CH$IsMsgInit" in form:
            # Old website
            eventtarget = "Menu_AJAX"
            scriptmanager = "m_ScriptManager|Menu_AJAX"
        else:
            # New website
            eventtarget = "MM$m_PostBack"
            scriptmanager = "MM$m_UpdatePanel|MM$m_PostBack"

        self.submit_form(form, eventargument, eventtarget, scriptmanager)

    def go_cards(self):
        # Do not try to go the card summary if we have no card, it breaks the session
        if self.browser.new_website and not CleanText('//form[@id="main"]//a/span[text()="Mes cartes bancaires"]')(self.doc):
            self.logger.info("Do not try to go the CardsPage, there is not link on the main page")
            return

        form = self.get_form(id='main')

        eventargument = ""

        if "MM$m_CH$IsMsgInit" in form:
            # Old website
            eventtarget = "Menu_AJAX"
            eventargument = "HISENCB0"
            scriptmanager = "m_ScriptManager|Menu_AJAX"
        else:
            # New website
            eventtarget = "MM$SYNTHESE$btnSyntheseCarte"
            scriptmanager = "MM$m_UpdatePanel|MM$SYNTHESE$btnSyntheseCarte"

        self.submit_form(form, eventargument, eventtarget, scriptmanager)

    # only for old website
    def go_card_coming(self, eventargument):
        form = self.get_form(id='main')
        eventtarget = "MM$HISTORIQUE_CB"
        scriptmanager = "m_ScriptManager|Menu_AJAX"
        self.submit_form(form, eventargument, eventtarget, scriptmanager)

    # only for new website
    def go_coming(self, eventargument):
        form = self.get_form(id='main')
        eventtarget = "MM$HISTORIQUE_CB"
        scriptmanager = "MM$m_UpdatePanel|MM$HISTORIQUE_CB"
        self.submit_form(form, eventargument, eventtarget, scriptmanager)

    # On some pages, navigate to indexPage does not lead to the list of measures, so we need this form ...
    def go_measure_list(self):
        form = self.get_form(id='main')

        form['__EVENTARGUMENT'] = "MESLIST0"
        form['__EVENTTARGET'] = 'Menu_AJAX'
        form['m_ScriptManager'] = 'm_ScriptManager|Menu_AJAX'

        fix_form(form)

        form.submit()

    # This function goes to the accounts page of one measure giving its id
    def go_measure_accounts_list(self, measure_id):
        form = self.get_form(id='main')

        form['__EVENTARGUMENT'] = "CPTSYNT0"

        if "MM$m_CH$IsMsgInit" in form:
            # Old website
            form['__EVENTTARGET'] = "MM$SYNTHESE_MESURES"
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$SYNTHESE_MESURES"
            form['__EVENTARGUMENT'] = measure_id
        else:
            # New website
            form['__EVENTTARGET'] = "MM$m_PostBack"
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$m_PostBack"

        fix_form(form)

        form.submit()

    def go_loan_list(self):
        form = self.get_form(id='main')

        form['__EVENTARGUMENT'] = "CRESYNT0"

        if "MM$m_CH$IsMsgInit" in form:
            # Old website
            pass
        else:
            # New website
            form['__EVENTTARGET'] = "MM$m_PostBack"
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$m_PostBack"

        fix_form(form)

        form.submit()

    def go_checkings(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$m_PostBack'
        form['__EVENTARGUMENT'] = 'CPTSYNT1'

        fix_form(form)
        form.submit()

    def go_transfer_list(self):
        form = self.get_form(id='main')

        form['__EVENTARGUMENT'] = 'HISVIR0&codeMenu=WVI3'
        form['__EVENTTARGET'] = 'MM$Menu_Ajax'

        fix_form(form)
        form.submit()

    @method
    class iter_transfers(TableElement):
        head_xpath = '//table[@summary="Liste des RICE à imprimer"]//th'
        item_xpath = '//table[@summary="Liste des RICE à imprimer"]//tr[td]'

        col_amount = 'Montant'
        col_recipient_label = 'Bénéficiaire'
        col_label = 'Référence'
        col_date = 'Date'

        class item(ItemElement):
            klass = Transfer

            obj_amount = CleanDecimal.French(TableCell('amount'))
            obj_recipient_label = CleanText(TableCell('recipient_label'))
            obj_label = CleanText(TableCell('label'))
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)

    def is_history_of(self, account_id):
        """
        Check whether the displayed history is for the correct account.
        If we do not find the select box we consider we are on the expected account (like it was before this check)
        """
        if self.doc.xpath('//select[@id="MM_HISTORIQUE_COMPTE_m_ExDropDownList"]'):
            return bool(self.doc.xpath('//option[@value="%s" and @selected]' % account_id))
        return True

    def go_history(self, info, is_cbtab=False):
        form = self.get_form(id='main')

        form['__EVENTTARGET'] = 'MM$%s' % (info['type'] if is_cbtab else 'SYNTHESE')
        form['__EVENTARGUMENT'] = info['link']

        if "MM$m_CH$IsMsgInit" in form and (form['MM$m_CH$IsMsgInit'] == "0" or info['type'] == 'ASSURANCE_VIE'):
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$SYNTHESE"

        fix_form(form)
        return form.submit()

    def go_history_netpro(self, info, ):
        """
        On the netpro website the go_history() does not work.
        Even from a web browser the site does not work, and display the history of the first account
        We use a different post to go through and display the history we need
        """
        form = self.get_form(id='main')
        form['m_ScriptManager'] = 'MM$m_UpdatePanel|MM$HISTORIQUE_COMPTE$m_ExDropDownList'
        form['MM$HISTORIQUE_COMPTE$m_ExDropDownList'] = info['id']
        form['__EVENTTARGET'] = 'MM$HISTORIQUE_COMPTE$m_ExDropDownList'

        fix_form(form)
        return form.submit()

    def get_form_to_detail(self, transaction):
        m = re.match('.*\("(.*)", "(DETAIL_OP&[\d]+).*\)\)', transaction._link)
        # go to detailcard page
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = m.group(1)
        form['__EVENTARGUMENT'] = m.group(2)
        fix_form(form)
        return form

    def get_history(self):
        i = 0
        ignore = False
        for tr in self.doc.xpath('//table[@cellpadding="1"]/tr') + self.doc.xpath('//tr[@class="rowClick" or @class="rowHover"]'):
            tds = tr.findall('td')

            if len(tds) < 4:
                continue

            # if there are more than 4 columns, ignore the first one.
            i = min(len(tds) - 4, 1)

            if tr.attrib.get('class', '') == 'DataGridHeader':
                if tds[2].text == 'Titulaire':
                    ignore = True
                else:
                    ignore = False
                continue

            if ignore:
                continue

            # Remove useless details
            detail = tr.xpath('.//div[has-class("detail")]')
            if len(detail) > 0:
                detail[0].drop_tree()

            t = Transaction()

            date = ''.join([txt.strip() for txt in tds[i + 0].itertext()])
            raw = ' '.join([txt.strip() for txt in tds[i + 1].itertext()])
            debit = ''.join([txt.strip() for txt in tds[-2].itertext()])
            credit = ''.join([txt.strip() for txt in tds[-1].itertext()])

            t.parse(date, re.sub(r'[ ]+', ' ', raw))

            card_debit_date = self.doc.xpath('//span[@id="MM_HISTORIQUE_CB_m_TableTitle3_lblTitle"] | //label[contains(text(), "débiter le")]')
            if card_debit_date:
                t.rdate = t.bdate = Date(dayfirst=True).filter(date)
                m = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', card_debit_date[0].text)
                assert m
                t.date = Date(dayfirst=True).filter(m.group(1))
            if t.date is NotAvailable:
                continue
            if any(l in t.raw.lower() for l in ('tot dif', 'fac cb')):
                t._link = Link(tr.xpath('./td/a'))(self.doc)

            # "Cb" for new site, "CB" for old one
            mtc = re.match(r'(Cb|CB) (\d{4}\*+\d{6}) ', raw)
            if mtc is not None:
                t.card = mtc.group(2)

            t.set_amount(credit, debit)
            yield t

            i += 1

    def go_next(self):
        # <a id="MM_HISTORIQUE_CB_lnkSuivante" class="next" href="javascript:WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions(&quot;MM$HISTORIQUE_CB$lnkSuivante&quot;, &quot;&quot;, true, &quot;&quot;, &quot;&quot;, false, true))">Suivant<span class="arrow">></span></a>

        link = self.doc.xpath('//a[contains(@id, "lnkSuivante")]')
        if len(link) == 0 or 'disabled' in link[0].attrib or link[0].attrib.get('class') == 'aspNetDisabled':
            return False

        account_type = 'COMPTE'
        m = re.search('HISTORIQUE_(\w+)', link[0].attrib['href'])
        if m:
            account_type = m.group(1)

        form = self.get_form(id='main')

        form['__EVENTTARGET'] = "MM$HISTORIQUE_%s$lnkSuivante" % account_type
        form['__EVENTARGUMENT'] = ''

        if "MM$m_CH$IsMsgInit" in form and form['MM$m_CH$IsMsgInit'] == "N":
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$HISTORIQUE_COMPTE$lnkSuivante"

        fix_form(form)
        form.submit()

        return True

    def go_life_insurance(self, account):
        # The site shows nothing about life insurance accounts except balance, links are disabled
        if 'measure_id' in account._info:
            return

        link = self.doc.xpath('//tr[td[contains(., ' + account.id + ') ]]//a')[0]
        m = re.search("PostBackOptions?\([\"']([^\"']+)[\"'],\s*['\"]((REDIR_ASS_VIE)?[\d\w&]+)?['\"]", link.attrib.get('href', ''))
        if m is not None:
            form = self.get_form(id='main')

            form['__EVENTTARGET'] = m.group(1)
            form['__EVENTARGUMENT'] = m.group(2)

            if "MM$m_CH$IsMsgInit" not in form:
                # Not available on new website
                pass

            form['MM$m_CH$IsMsgInit'] = "0"
            form['m_ScriptManager'] = "MM$m_UpdatePanel|MM$SYNTHESE"

            fix_form(form)
            form.submit()

    def transfer_link(self):
        return self.doc.xpath('//a[span[contains(text(), "Effectuer un virement")]] | //a[contains(text(), "Réaliser un virement")]')

    def go_transfer_via_history(self, account):
        self.go_history(account._info)

        # check that transfer is available for the connection before try to go on transfer page
        # otherwise website will continually crash
        if self.transfer_link():
            self.browser.page.go_transfer(account)

    def go_transfer_page(self):
        link = self.transfer_link()
        if len(link) == 0:
            return False
        else:
            link = link[0]
        m = re.search("PostBackOptions?\([\"']([^\"']+)[\"'],\s*['\"]([^\"']+)?['\"]", link.attrib.get('href', ''))
        form = self.get_form(id='main')
        if 'MM$HISTORIQUE_COMPTE$btnCumul' in form:
            del form['MM$HISTORIQUE_COMPTE$btnCumul']
        form['__EVENTTARGET'] = m.group(1)
        form['__EVENTARGUMENT'] = m.group(2)
        form.submit()

    def go_transfer(self, account):
        if self.go_transfer_page() is False:
            return self.go_transfer_via_history(account)

    def go_emitters(self):
        return self.go_transfer_page()

    def transfer_unavailable(self):
        return CleanText('//li[contains(text(), "Pour accéder à cette fonctionnalité, vous devez disposer d’un moyen d’authentification renforcée")]')(self.doc)

    def loan_unavailable_msg(self):
        msg = CleanText('//span[@id="MM_LblMessagePopinError"] | //p[@id="MM_ERREUR_PAGE_BLANCHE_pAlert"]')(self.doc)
        if msg:
            return msg

    def is_subscription_unauthorized(self):
        return 'non autorisée' in CleanText('//div[@id="MM_ContentMain"]')(self.doc)

    def go_pro_transfer_availability(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'Menu_AJAX'
        form['__EVENTARGUMENT'] = 'VIRLSRM0'
        form['m_ScriptManager'] = 'm_ScriptManager|Menu_AJAX'
        form.submit()

    def is_transfer_allowed(self):
        return not self.doc.xpath('//ul/li[contains(text(), "Aucun compte tiers n\'est disponible")]')

    def levies_page_enabled(self):
        """ Levies page does not exist in the nav bar for every connections """
        return (
            CleanText('//a/span[contains(text(), "Suivre mes prélèvements reçus")]')(self.doc)  # new website
            or CleanText('//a[contains(text(), "Suivre les prélèvements reçus")]')(self.doc)  # old website
        )

    def get_trusted_device_url(self):
        return Regexp(
            CleanText('//script[contains(text(), "trusted-device")]'),
            r'if\("([^"]+(?:trusted-device)[^"]+)"',
            default=None,
        )(self.doc)


class TransactionPopupPage(LoggedPage, HTMLPage):
    def is_here(self):
        return CleanText('''//div[@class="scrollPane"]/table[//caption[contains(text(), "Détail de l'opération")]]''')(self.doc)

    def complete_label(self):
        return CleanText('''//div[@class="scrollPane"]/table[//caption[contains(text(), "Détail de l'opération")]]//tr[2]''')(self.doc)


class NewLeviesPage(IndexPage):
    """ Scrape new website 'Prélèvements' page for comings for checking accounts """

    def is_here(self):
        return CleanText('//h2[contains(text(), "Suivez vos prélèvements reçus")]')(self.doc)

    def comings_enabled(self, account_id):
        """ Check if a specific account can be selected on the general levies page """
        return account_id in CleanText('//span[@id="MM_SYNTHESE_SDD_RECUS"]//select/option/@value')(self.doc)

    @method
    class iter_coming(TableElement):
        head_xpath = '//div[contains(@id, "ListePrelevement_0")]/table[contains(@summary, "Liste des prélèvements en attente")]//tr/th'
        item_xpath = '//div[contains(@id, "ListePrelevement_0")]/table[contains(@summary, "Liste des prélèvements en attente")]//tr[contains(@id, "trRowDetail")]'

        col_label = 'Libellé/Référence'
        col_coming = 'Montant'
        col_date = 'Date'

        class item(ItemElement):
            klass = Transaction

            # Transaction typing will mostly not work since transaction as comings will only display the debiting organism in the label
            # Labels will bear recognizable patterns only when they move from future to past, where they will be typed by iter_history
            # when transactions change state from coming to history 'Prlv' is append to their label, this will help the backend for the matching
            obj_raw = Transaction.Raw(Format('Prlv %s', Field('label')))
            obj_label = CleanText(TableCell('label'))
            obj_amount = CleanDecimal.French(TableCell('coming'), sign=lambda x: -1)
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)

            def condition(self):
                return not CleanText('''//p[contains(text(), "Vous n'avez pas de prélèvement en attente d'exécution.")]''')(self)


class OldLeviesPage(IndexPage):
    """ Scrape old website 'Prélèvements' page for comings for checking accounts """

    def is_here(self):
        return CleanText('//span[contains(text(), "Suivez vos prélèvements reçus")]')(self.doc)

    def comings_enabled(self, account_id):
        """ Check if a specific account can be selected on the general levies page """
        return account_id in CleanText('//span[@id="MM_SYNTHESE_SDD_RECUS"]//select/option/@value')(self.doc)

    @method
    class iter_coming(TableElement):
        head_xpath = '''//span[contains(text(), "Prélèvements en attente d'exécution")]/ancestor::table[1]/following-sibling::table[1]//tr[contains(@class, "DataGridHeader")]//td'''
        item_xpath = '''//span[contains(text(), "Prélèvements en attente d'exécution")]/ancestor::table[1]/following-sibling::table[1]//tr[contains(@class, "DataGridHeader")]//following-sibling::tr'''

        col_label = 'Libellé/Référence'
        col_coming = 'Montant'
        col_date = 'Date'

        class item(ItemElement):
            klass = Transaction

            # Transaction typing will mostly not work since transaction as comings will only display the debiting organism in the label
            # Labels will bear recognizable patterns only when they move from future to past, where they will be typed by iter_history
            # when transactions change state from coming to history 'Prlv' is append to their label, this will help the backend for the matching
            obj_raw = Transaction.Raw(Format('Prlv %s', Field('label')))
            obj_label = CleanText(TableCell('label'))
            obj_amount = CleanDecimal.French(TableCell('coming'), sign=lambda x: -1)
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)

            def condition(self):
                return not CleanText('''//table[@id="MM_SYNTHESE_SDD_RECUS_rpt_dgList_0"]//td[contains(text(), "Vous n'avez pas de prélèvements")]''')(self)


class CardsPage(IndexPage):
    def is_here(self):
        return CleanText('//h3[normalize-space(text())="Mes cartes (cartes dont je suis le titulaire)"]')(self.doc)

    @method
    class iter_cards(TableElement):
        head_xpath = '//table[@class="cartes"]/tbody/tr/th'

        col_label = 'Carte'
        col_number = 'N°'
        col_parent = 'Compte dépot associé'
        col_coming = 'Encours'

        item_xpath = '//table[@class="cartes"]/tbody/tr[not(th)]'

        class item(ItemElement):
            klass = Account

            obj_type = Account.TYPE_CARD
            obj_label = Format('%s %s', CleanText(TableCell('label')), Field('id'))
            obj_number = CleanText(TableCell('number'))
            obj_id = CleanText(TableCell('number'), replace=[('*', 'X')])
            obj__parent_id = CleanText(TableCell('parent'))
            obj_balance = 0
            obj_currency = Currency(TableCell('coming'))
            obj__card_links = None

            def obj_coming(self):
                if CleanText(TableCell('coming'))(self) == '-':
                    raise SkipItem('immediate debit card?')
                return CleanDecimal.French(TableCell('coming'), sign=lambda x: -1)(self)

            def condition(self):
                immediate_str = ''
                # There are some card without any information. To exclude them, we keep only account
                # with extra "option" (ex: coming transaction link, block bank card...)
                if 'Faire opposition' in CleanText("./td[5]")(self):
                    # Only deferred card have this option to see coming transaction, even when
                    # there is 0 coming (Table element have no thead for the 5th column).
                    if 'Consulter mon encours carte' in CleanText("./td[5]")(self):
                        return True

                    # Card without 'Consulter mon encours carte' are immediate card. There are logged
                    # for now to make the debug easier
                    immediate_str = '[Immediate card]'

                self.logger.warning('Skip card %s (no history/coming information) %s', Field('number')(self), immediate_str)
                return False


class CardsComingPage(IndexPage):
    def is_here(self):
        return CleanText('//h2[text()="Encours de carte à débit différé"]')(self.doc)

    @method
    class iter_cards(ListElement):
        item_xpath = '//table[contains(@class, "compte") and position() = 1]//tr[contains(@id, "MM_HISTORIQUE_CB") and position() < last()]'

        class item(ItemElement):
            klass = Account

            def obj_id(self):
                # We must handle two kinds of Regexp because the 'X' are not
                # located at the same level for sub-modules such as palatine
                return Coalesce(
                    Regexp(CleanText(Field('label'), replace=[('*', 'X')]), r'(\d{6}X{6}\d{4})', default=NotAvailable),
                    Regexp(CleanText(Field('label'), replace=[('*', 'X')]), r'(\d{4}X{6}\d{6})', default=NotAvailable),
                )(self)

            def obj_number(self):
                return Coalesce(
                    Regexp(CleanText(Field('label')), r'(\d{6}\*{6}\d{4})', default=NotAvailable),
                    Regexp(CleanText(Field('label')), r'(\d{4}\*{6}\d{6})', default=NotAvailable),
                )(self)

            obj_type = Account.TYPE_CARD
            obj_label = CleanText('./td[1]')
            obj_balance = Decimal(0)
            obj_coming = CleanDecimal.French('./td[2]')
            obj_currency = Currency('./td[2]')
            obj__card_links = None

    def get_card_coming_info(self, number, info):
        # If the xpath match, that mean there are only one card
        # We have enough information in `info` to get its coming transaction
        if CleanText('//tr[@id="MM_HISTORIQUE_CB_rptMois0_ctl01_trItem"]')(self.doc):
            return info

        # If the xpath match, that mean there are at least 2 cards
        xpath = '//tr[@id="MM_HISTORIQUE_CB_rptMois0_trItem_0"]'

        # In case of multiple card, first card coming's transactions are reachable
        # with information in `info`.
        if Regexp(CleanText(xpath), r'(\d{6}\*{6}\d{4})')(self.doc) == number:
            return info

        # Some cards redirect to a checking account where we cannot found them. Since we have no details or history,
        # we return None and skip them in the browser.
        if CleanText('//a[contains(text(),"%s")]' % number)(self.doc):
            # For all cards except the first one for the same check account, we have to get info through their href info
            link = CleanText(Link('//a[contains(text(),"%s")]' % number))(self.doc)
            infos = re.match(r'.*(DETAIL_OP_M\d&[^\"]+).*', link)
            info['link'] = infos.group(1)

            return info
        return None


class CardsOldWebsitePage(IndexPage):
    def is_here(self):
        return CleanText('//span[@id="MM_m_CH_lblTitle" and contains(text(), "Historique de vos encours CB")]')(self.doc)

    def get_account(self):
        infos = CleanText('.//span[@id="MM_HISTORIQUE_CB"]/table[position()=1]//td')(self.doc)
        result = re.search(r'.*(\d{11}).*', infos)
        return result.group(1)

    def get_date(self):
        title = CleanText('//span[@id="MM_HISTORIQUE_CB_m_TableTitle3_lblTitle"]')(self.doc)
        title_date = re.match('.*le (.*) sur .*', title)
        return Date(dayfirst=True).filter(title_date.group(1))

    @method
    class iter_cards(TableElement):
        head_xpath = '//table[@id="MM_HISTORIQUE_CB_m_ExDGOpeM0"]//tr[@class="DataGridHeader"]/td'
        item_xpath = '//table[@id="MM_HISTORIQUE_CB_m_ExDGOpeM0"]//tr[not(contains(@class, "DataGridHeader")) and position() < last()]'

        col_label = 'Libellé'
        col_coming = 'Solde'

        class item(ItemElement):
            klass = Account

            obj_type = Account.TYPE_CARD
            obj_label = Format('%s %s', CleanText(TableCell('label')), CleanText(Field('number')))
            obj_balance = 0
            obj_coming = CleanDecimal.French(TableCell('coming'))
            obj_currency = Currency(TableCell('coming'))
            obj__card_links = None

            def obj__parent_id(self):
                return self.page.get_account()

            def obj_number(self):
                return CleanText(TableCell('number'))(self).replace('*', 'X')

            def obj_id(self):
                number = Field('number')(self).replace('X', '')
                account_id = '%s-%s' % (self.obj__parent_id(), number)
                return account_id

            def obj__coming_eventargument(self):
                url = Attr('.//a', 'href')(self)
                res = re.match(r'.*(DETAIL_OP_M0\&.*;\d{8})", .*', url)
                return res.group(1)

        def parse(self, obj):
            # There are no thead name for this column.
            self._cols['number'] = 3

    @method
    class iter_coming(TableElement):
        head_xpath = '//table[@id="MM_HISTORIQUE_CB_m_ExDGDetailOpe"]//tr[@class="DataGridHeader"]/td'
        item_xpath = '//table[@id="MM_HISTORIQUE_CB_m_ExDGDetailOpe"]//tr[not(contains(@class, "DataGridHeader"))]'

        col_label = 'Libellé'
        col_coming = 'Débit'
        col_date = 'Date'

        class item(ItemElement):
            klass = Transaction

            obj_type = Transaction.TYPE_DEFERRED_CARD
            obj_label = CleanText(TableCell('label'))
            obj_amount = CleanDecimal.French(TableCell('coming'), sign=lambda x: -1)
            obj_rdate = obj_bdate = Date(CleanText(TableCell('date')), dayfirst=True)

            def obj_date(self):
                return self.page.get_date()


class ConsLoanPage(JsonPage):
    def get_conso(self):
        return self.doc


class LoadingPage(HTMLPage):
    def on_load(self):
        # CTX cookie seems to corrupt the request fetching info about "credit
        # renouvelable" and to lead to a 409 error
        if 'CTX' in self.browser.session.cookies.keys():
            del self.browser.session.cookies['CTX']

        form = self.get_form(id="REROUTAGE")
        form.submit()


class NatixisRedirectPage(LoggedPage, HTMLPage):
    def on_load(self):
        try:
            form = self.get_form(id="NaAssurance")
        except FormNotFound:
            form = self.get_form(id="formRoutage")
        form.submit()


class MarketPage(LoggedPage, HTMLPage):
    def is_error(self):
        return CleanText('//caption[contains(text(),"Erreur")]')(self.doc)

    def parse_decimal(self, td, percentage=False):
        value = CleanText('.')(td)
        if value and value != '-':
            if percentage:
                return Decimal(FrenchTransaction.clean_amount(value)) / 100
            return Decimal(FrenchTransaction.clean_amount(value))
        else:
            return NotAvailable

    def submit(self):
        form = self.get_form(nr=0)

        form.submit()

    def iter_investment(self):
        for tbody in self.doc.xpath('//table[@summary="Contenu du portefeuille valorisé"]/tbody'):
            inv = Investment()
            inv.label = CleanText('.')(tbody.xpath('./tr[1]/td[1]/a/span')[0])
            inv.code = CleanText('.')(tbody.xpath('./tr[1]/td[1]/a')[0]).split(' - ')[1]
            inv.code_type = Investment.CODE_TYPE_ISIN if is_isin_valid(inv.code) else NotAvailable
            inv.quantity = self.parse_decimal(tbody.xpath('./tr[2]/td[2]')[0])
            inv.unitvalue = self.parse_decimal(tbody.xpath('./tr[2]/td[3]')[0])
            inv.unitprice = self.parse_decimal(tbody.xpath('./tr[2]/td[5]')[0])
            inv.valuation = self.parse_decimal(tbody.xpath('./tr[2]/td[4]')[0])
            inv.diff = self.parse_decimal(tbody.xpath('./tr[2]/td[7]')[0])

            yield inv

    def get_valuation_diff(self, account):
        val = CleanText(self.doc.xpath('//td[contains(text(), "values latentes")]/following-sibling::*[1]'))
        account.valuation_diff = CleanDecimal(Regexp(val, '([^\(\)]+)'), replace_dots=True)(self)

    def is_on_right_portfolio(self, account):
        return len(self.doc.xpath('//form[@class="choixCompte"]//option[@selected and contains(text(), $id)]', id=account._info['id']))

    def get_compte(self, account):
        return self.doc.xpath('//option[contains(text(), $id)]/@value', id=account._info['id'])[0]

    def come_back(self):
        link = Link('//div/a[contains(text(), "Accueil accès client")]', default=NotAvailable)(self.doc)
        if link:
            self.browser.location(link)


class LifeInsurance(MarketPage):
    pass


class LifeInsuranceHistory(LoggedPage, JsonPage):
    def build_doc(self, text):
        # If history is empty, there is no text
        if not text:
            return {}
        return super(LifeInsuranceHistory, self).build_doc(text)

    @method
    class iter_history(DictElement):
        def find_elements(self):
            return self.el or []  # JSON contains 'null' if no transaction

        class item(ItemElement):
            klass = Transaction

            def condition(self):
                # Eliminate transactions without amount
                return Dict('montantBrut')(self)

            obj_raw = Transaction.Raw(Dict('type/libelleLong'))
            obj_amount = Eval(float_to_decimal, Dict('montantBrut/valeur'))

            def obj_date(self):
                date = Dict('dateTraitement')(self)
                if date:
                    return datetime.fromtimestamp(date / 1000)
                return NotAvailable

            obj_rdate = obj_date

            def obj_vdate(self):
                vdate = Dict('dateEffet')(self)
                if vdate:
                    return datetime.fromtimestamp(vdate / 1000)
                return NotAvailable


class LifeInsuranceInvestments(LoggedPage, JsonPage):
    @method
    class iter_investment(DictElement):

        def find_elements(self):
            return self.el['repartition']['supports'] or []  # JSON contains 'null' if no investment

        class item(ItemElement):
            klass = Investment

            # For whatever reason some labels start with a '.' (for example '.INVESTMENT')
            obj_label = CleanText(Dict('libelleSupport'), replace=[('.', '')])
            obj_valuation = Eval(float_to_decimal, Dict('montantBrutInvesti/valeur'))

            def obj_portfolio_share(self):
                invested_percentage = Dict('pourcentageInvesti', default=None)(self)
                if invested_percentage:
                    return float_to_decimal(invested_percentage) / 100
                return NotAvailable

            # Note: the following attributes are not available for euro funds
            def obj_vdate(self):
                vdate = Dict('cotation/date')(self)
                if vdate:
                    return datetime.fromtimestamp(vdate / 1000)
                return NotAvailable

            def obj_quantity(self):
                if Dict('nombreParts')(self):
                    return Eval(float_to_decimal, Dict('nombreParts'))(self)
                return NotAvailable

            def obj_diff(self):
                if Dict('montantPlusValue/valeur', default=None)(self):
                    return Eval(float_to_decimal, Dict('montantPlusValue/valeur'))(self)
                return NotAvailable

            def obj_diff_ratio(self):
                if Dict('tauxPlusValue')(self):
                    return Eval(lambda x: float_to_decimal(x) / 100, Dict('tauxPlusValue'))(self)
                return NotAvailable

            def obj_unitvalue(self):
                if Dict('cotation/montant')(self):
                    return Eval(float_to_decimal, Dict('cotation/montant/valeur'))(self)
                return NotAvailable

            def obj_code(self):
                code = Dict('codeISIN')(self)
                if is_isin_valid(code):
                    return code
                return NotAvailable

            def obj_code_type(self):
                if Field('code')(self) == NotAvailable:
                    return NotAvailable
                return Investment.CODE_TYPE_ISIN


class NatixisLIHis(LoggedPage, JsonPage):
    @method
    class get_history(DictElement):
        item_xpath = None

        class item(ItemElement):
            klass = Transaction

            obj_amount = Eval(float_to_decimal, Dict('montantNet'))
            obj_raw = CleanText(Dict('libelle', default=''))
            obj_vdate = Date(Dict('dateValeur', default=NotAvailable), default=NotAvailable)
            obj_date = Date(Dict('dateEffet', default=NotAvailable), default=NotAvailable)
            obj_investments = NotAvailable
            obj_type = Transaction.TYPE_BANK

            def validate(self, obj):
                return obj.raw and obj.date


class NatixisLIInv(LoggedPage, JsonPage):
    @method
    class get_investments(DictElement):
        item_xpath = 'detailContratVie/valorisation/supports'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(Dict('nom'))
            obj_code = CleanText(Dict('codeIsin'))

            def obj_vdate(self):
                dt = Dict('dateValeurUniteCompte', default=None)(self)
                if dt is None:
                    dt = self.page.doc['detailContratVie']['valorisation']['date']
                return Date().filter(dt)

            obj_valuation = Eval(float_to_decimal, Dict('montant'))
            obj_quantity = Eval(float_to_decimal, Dict('nombreUnitesCompte'))
            obj_unitvalue = Eval(float_to_decimal, Dict('valeurUniteCompte'))

            def obj_portfolio_share(self):
                repartition = Dict('repartition', default=None)(self)
                if repartition:
                    return float_to_decimal(repartition) / 100
                return NotAvailable


class MyRecipient(ItemElement):
    klass = Recipient

    # Assume all recipients currency is euros.
    obj_currency = 'EUR'

    def obj_enabled_at(self):
        return datetime.now().replace(microsecond=0)


class MyEmitter(ItemElement):
    klass = Emitter

    obj_id = Attr('.', 'value')
    obj_currency = Currency('.')
    obj_number_type = EmitterNumberType.IBAN

    def obj_number(self):
        return rib2iban(Attr('.', 'value')(self))


class MyEmitters(ListElement):
    item_xpath = '//select[@id="MM_VIREMENT_SAISIE_VIREMENT_ddlCompteDebiter"]/option'

    class Item(MyEmitter):
        pass


class TransferErrorPage(object):
    def on_load(self):
        errors_xpaths = [
            '//div[h2[text()="Information"]]/p[contains(text(), "Il ne pourra pas être crédité avant")]',
            '//span[@id="MM_LblMessagePopinError"]/p | //div[h2[contains(text(), "Erreur de saisie")]]/p[1] | //span[@class="error"]/strong',
            '//div[@id="MM_m_CH_ValidationSummary" and @class="MessageErreur"]',
        ]

        for error_xpath in errors_xpaths:
            error = CleanText(error_xpath)(self.doc)
            if error:
                raise TransferBankError(message=error)


class MeasurePage(IndexPage):
    def is_here(self):
        return self.doc.xpath('//span[contains(text(), "Liste de vos mesures")]')


class MyRecipients(ListElement):
    def parse(self, obj):
        self.item_xpath = self.page.RECIPIENT_XPATH

    class Item(MyRecipient):
        def validate(self, obj):
            return self.obj_id(self) != self.env['account_id']

        obj_id = Env('id')
        obj_iban = Env('iban')
        obj_bank_name = Env('bank_name')
        obj_category = Env('category')
        obj_label = Env('label')

        def parse(self, el):
            value = Attr('.', 'value')(self)
            # Autres comptes
            if value == 'AC':
                raise SkipItem()
            self.env['category'] = 'Interne' if value[0] == 'I' else 'Externe'
            if self.env['category'] == 'Interne':
                # TODO use after 'I'?
                _id = Regexp(CleanText('.'), r'- (\w+\d\w+)')(self)  # at least one digit
                accounts = list(self.page.browser.get_accounts_list()) + list(self.page.browser.get_loans_list())
                # If it's an internal account, we should always find only one account with _id in it's id.
                # Type card account contains their parent account id, and should not be listed in recipient account.
                match = [acc for acc in accounts if _id in acc.id and acc.type != Account.TYPE_CARD]
                assert len(match) == 1
                match = match[0]
                self.env['id'] = match.id
                self.env['iban'] = match.iban
                self.env['bank_name'] = u"Caisse d'Épargne"
                self.env['label'] = match.label
            # Usual case `E-` or `UE-`
            elif value[1] == '-' or value[2] == '-':
                full = CleanText('.')(self)
                if full.startswith('- '):
                    self.logger.warning('skipping recipient without a label: %r', full)
                    raise SkipItem()

                # <recipient name> - <account number or iban> - <bank name (optional)>
                # bank name can have one dash, multiple dots in their names or just be a dash (seen in palatine, example below)
                # eg: ING-DiBan / C.PROF. / B.R.E.D
                # Seen in palatine (the bank name can be a dash): <recipient name> - <iban> - -
                mtc = re.match(r'(?P<label>.+) - (?P<id>[^-]+) - ?(?P<bank>[^-]+-?[\w\. ]+)?-?$', full)
                assert mtc, "Unhandled recipient's label/iban/bank name format"
                self.env['id'] = self.env['iban'] = mtc.group('id')
                self.env['bank_name'] = (mtc.group('bank') and mtc.group('bank').strip()) or NotAvailable
                self.env['label'] = mtc.group('label')
            # Fcking corner case
            else:
                # former regex: '(?P<id>.+) - (?P<label>[^-]+) -( [^-]*)?-?$'
                # the strip is in case the string ends by ' -'
                mtc = CleanText('.')(self).strip(' -').split(' - ')
                # it needs to contain, at least, the id and the label
                assert len(mtc) >= 2
                self.env['id'] = mtc[0]
                self.env['iban'] = NotAvailable
                self.env['bank_name'] = NotAvailable
                self.env['label'] = mtc[1]


class TransferPage(TransferErrorPage, IndexPage):
    RECIPIENT_XPATH = '//select[@id="MM_VIREMENT_SAISIE_VIREMENT_ddlCompteCrediter"]/option'

    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Effectuer un virement")]')(self.doc))

    def can_transfer(self, account):
        for o in self.doc.xpath('//select[@id="MM_VIREMENT_SAISIE_VIREMENT_ddlCompteDebiter"]/option'):
            if Regexp(CleanText('.'), '- (\d+)')(o) in account.id:
                return True

    def get_origin_account_value(self, account):
        origin_value = [Attr('.', 'value')(o) for o in self.doc.xpath('//select[@id="MM_VIREMENT_SAISIE_VIREMENT_ddlCompteDebiter"]/option') if
                        Regexp(CleanText('.'), '- (\d+)')(o) in account.id]
        assert len(origin_value) == 1, 'error during origin account matching'
        return origin_value[0]

    def get_recipient_value(self, recipient):
        if recipient.category == 'Externe':
            recipient_value = [Attr('.', 'value')(o) for o in self.doc.xpath(self.RECIPIENT_XPATH) if
                               Regexp(CleanText('.'), '.* - ([A-Za-z0-9]*) -', default=NotAvailable)(o) == recipient.iban]
        elif recipient.category == 'Interne':
            recipient_value = [Attr('.', 'value')(o) for o in self.doc.xpath(self.RECIPIENT_XPATH) if
                               Regexp(CleanText('.'), '- (\d+)', default=NotAvailable)(o) and Regexp(CleanText('.'), '- (\d+)', default=NotAvailable)(o) in recipient.id]
        assert len(recipient_value) == 1, 'error during recipient matching'
        return recipient_value[0]

    def init_transfer(self, account, recipient, transfer):
        form = self.get_form(id='main')
        form['MM$VIREMENT$SAISIE_VIREMENT$ddlCompteDebiter'] = self.get_origin_account_value(account)
        form['MM$VIREMENT$SAISIE_VIREMENT$ddlCompteCrediter'] = self.get_recipient_value(recipient)
        form['MM$VIREMENT$SAISIE_VIREMENT$txtLibelleVirement'] = transfer.label
        form['MM$VIREMENT$SAISIE_VIREMENT$txtMontant$m_txtMontant'] = unicode(transfer.amount)
        form['__EVENTTARGET'] = 'MM$VIREMENT$m_WizardBar$m_lnkNext$m_lnkButton'
        if transfer.exec_date != datetime.today().date():
            form['MM$VIREMENT$SAISIE_VIREMENT$radioVirement'] = 'differe'
            form['MM$VIREMENT$SAISIE_VIREMENT$m_DateDiffere$txtDate'] = transfer.exec_date.strftime('%d/%m/%Y')
        form.submit()

    @method
    class iter_recipients(MyRecipients):
        pass

    def get_transfer_type(self):
        sepa_inputs = self.doc.xpath('//input[contains(@id, "MM_VIREMENT_SAISIE_VIREMENT_SEPA")]')
        intra_inputs = self.doc.xpath('//input[contains(@id, "MM_VIREMENT_SAISIE_VIREMENT_INTRA")]')

        assert not (len(sepa_inputs) and len(intra_inputs)), 'There are sepa and intra transfer forms'

        transfer_type = None
        if len(sepa_inputs):
            transfer_type = 'sepa'
        elif len(intra_inputs):
            transfer_type = 'intra'
        assert transfer_type, 'Sepa nor intra transfer form was found'
        return transfer_type

    def continue_transfer(self, origin_label, recipient_label, label):
        form = self.get_form(id='main')

        transfer_type = self.get_transfer_type()
        fill = lambda s, t: s % (t.upper(), t.capitalize())
        form['__EVENTTARGET'] = 'MM$VIREMENT$m_WizardBar$m_lnkNext$m_lnkButton'
        form[fill('MM$VIREMENT$SAISIE_VIREMENT_%s$m_Virement%s$txtIdentBenef', transfer_type)] = recipient_label
        form[fill('MM$VIREMENT$SAISIE_VIREMENT_%s$m_Virement%s$txtIdent', transfer_type)] = origin_label
        form[fill('MM$VIREMENT$SAISIE_VIREMENT_%s$m_Virement%s$txtRef', transfer_type)] = label
        form[fill('MM$VIREMENT$SAISIE_VIREMENT_%s$m_Virement%s$txtMotif', transfer_type)] = label
        form.submit()

    def go_add_recipient(self):
        form = self.get_form(id='main')
        link = self.doc.xpath('//a[span[contains(text(), "Ajouter un compte bénéficiaire")]]')[0]
        m = re.search("PostBackOptions?\([\"']([^\"']+)[\"'],\s*['\"]([^\"']+)?['\"]", link.attrib.get('href', ''))
        form['__EVENTTARGET'] = m.group(1)
        form['__EVENTARGUMENT'] = m.group(2)
        form.submit()

    def handle_error(self):
        # the website cannot add recipients from out of France
        error_msg = CleanText('//div[@id="divPopinInfoAjout"]/p[not(a)]')(self.doc)
        if error_msg:
            raise AddRecipientBankError(message=error_msg)

    @method
    class iter_emitters(MyEmitters):

        class Item(MyEmitter):

            def obj_label(self):
                """
                Label looks like 'Mr Dupont Jean C.cheque - 52XXX87 + 176,12 €'.
                We only keep the first half (name and account name).
                What's left is: 'Mr Dupont Jean C.cheque'
                """
                raw_string = CleanText('.')(self)
                if '-' in raw_string:
                    return raw_string.split('-')[0]
                return raw_string

            def obj_balance(self):
                attribute_data = Attr('.', 'data-ce-html', default=None)(self)
                return CleanDecimal.French('//span')(html.fromstring(attribute_data))


class TransferConfirmPage(TransferErrorPage, IndexPage):
    def build_doc(self, content):
        # The page have some <wbr> tags in the label content (spaces added each 40 characters if the character is not a space).
        # Consequently the label can't be matched with the original one. We delete these tags.
        content = content.replace(b'<wbr>', b'')
        return super(TransferErrorPage, self).build_doc(content)

    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Confirmer mon virement")]')(self.doc))

    def confirm(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$VIREMENT$m_WizardBar$m_lnkNext$m_lnkButton'
        form.submit()

    def update_transfer(self, transfer, account=None, recipient=None):
        """update `Transfer` object with web information to use transfer check"""

        # transfer informations
        transfer.label = (
            CleanText('.//tr[td[contains(text(), "Motif de l\'opération")]]/td[not(@class)]')(self.doc)
            or CleanText('.//tr[td[contains(text(), "Libellé")]]/td[not(@class)]')(self.doc)
            or CleanText('.//tr[th[contains(text(), "Libellé")]]/td[not(@class)]')(self.doc)
        )
        transfer.exec_date = Date(CleanText('.//tr[th[contains(text(), "En date du")]]/td[not(@class)]'), dayfirst=True)(self.doc)
        transfer.amount = CleanDecimal('.//tr[td[contains(text(), "Montant")]]/td[not(@class)] | \
                                        .//tr[th[contains(text(), "Montant")]]/td[not(@class)]', replace_dots=True)(self.doc)
        transfer.currency = FrenchTransaction.Currency('.//tr[td[contains(text(), "Montant")]]/td[not(@class)] | \
                                                        .//tr[th[contains(text(), "Montant")]]/td[not(@class)]')(self.doc)

        # recipient transfer informations, update information if there is no OTP SMS validation
        if recipient:
            transfer.recipient_label = recipient.label
            transfer.recipient_id = recipient.id

            if recipient.category == 'Externe':
                for word in Upper(CleanText('.//tr[th[contains(text(), "Compte à créditer")]]/td[not(@class)]'))(self.doc).split():
                    if is_iban_valid(word):
                        transfer.recipient_iban = word
                        break
                else:
                    assert False, 'Unable to find IBAN (original was %s)' % recipient.iban
            else:
                transfer.recipient_iban = recipient.iban

        # origin account transfer informations, update information if there is no OTP SMS validation
        if account:
            transfer.account_id = account.id
            transfer.account_iban = account.iban
            transfer.account_label = account.label
            transfer.account_balance = account.balance

        return transfer


class ProTransferConfirmPage(TransferConfirmPage):
    def is_here(self):
        return bool(CleanText('//span[@id="MM_m_CH_lblTitle" and contains(text(), "Confirmez votre virement")]')(self.doc))

    def continue_transfer(self, origin_label, recipient, label):
        # Pro internal transfer initiation doesn't need a second step.
        pass

    def create_transfer(self, account, recipient, transfer):
        t = Transfer()
        t.currency = FrenchTransaction.Currency('//span[@id="MM_VIREMENT_CONF_VIREMENT_MontantVir"] | \
                                                 //span[@id="MM_VIREMENT_CONF_VIREMENT_lblMontantSelect"]')(self.doc)
        t.amount = CleanDecimal('//span[@id="MM_VIREMENT_CONF_VIREMENT_MontantVir"] | \
                                 //span[@id="MM_VIREMENT_CONF_VIREMENT_lblMontantSelect"]', replace_dots=True)(self.doc)
        t.account_iban = account.iban
        if recipient.category == 'Externe':
            for word in Upper(CleanText('//span[@id="MM_VIREMENT_CONF_VIREMENT_lblCptCrediterResult"]'))(self.doc).split():
                if is_iban_valid(word):
                    t.recipient_iban = word
                    break
            else:
                assert False, 'Unable to find IBAN (original was %s)' % recipient.iban
        else:
            t.recipient_iban = recipient.iban
        t.recipient_iban = recipient.iban
        t.account_id = unicode(account.id)
        t.recipient_id = unicode(recipient.id)
        t.account_label = account.label
        t.recipient_label = recipient.label
        t._account = account
        t._recipient = recipient
        t.label = CleanText('//span[@id="MM_VIREMENT_CONF_VIREMENT_Libelle"] | \
                             //span[@id="MM_VIREMENT_CONF_VIREMENT_lblMotifSelect"]')(self.doc)
        t.exec_date = Date(CleanText('//span[@id="MM_VIREMENT_CONF_VIREMENT_DateVir"]'), dayfirst=True)(self.doc)
        t.account_balance = account.balance
        return t


class TransferSummaryPage(TransferErrorPage, IndexPage):
    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Accusé de réception")]')(self.doc))

    def populate_reference(self, transfer):
        transfer.id = Regexp(CleanText('//p[contains(text(), "a bien été enregistré")]'), '(\d+)')(self.doc)
        return transfer


class ProTransferSummaryPage(TransferErrorPage, IndexPage):
    def is_here(self):
        return bool(CleanText('//span[@id="MM_m_CH_lblTitle" and contains(text(), "Accusé de réception")]')(self.doc))

    def populate_reference(self, transfer):
        transfer.id = Regexp(CleanText('//span[@id="MM_VIREMENT_AR_VIREMENT_lblVirementEnregistre"]'), '(\d+( - \d+)?)')(self.doc)
        return transfer


class ProTransferPage(TransferPage):
    RECIPIENT_XPATH = '//select[@id="MM_VIREMENT_SAISIE_VIREMENT_ddlCompteCrediterPro"]/option'

    def is_here(self):
        return CleanText('//span[contains(text(), "Créer une liste de virements")] | //span[contains(text(), "Réalisez un virement")]')(self.doc)

    @method
    class iter_recipients(MyRecipients):
        pass

    def init_transfer(self, account, recipient, transfer):
        form = self.get_form(id='main')
        form['MM$VIREMENT$SAISIE_VIREMENT$ddlCompteDebiter'] = self.get_origin_account_value(account)
        form['MM$VIREMENT$SAISIE_VIREMENT$ddlCompteCrediterPro'] = self.get_recipient_value(recipient)
        form['MM$VIREMENT$SAISIE_VIREMENT$Libelle'] = transfer.label
        form['MM$VIREMENT$SAISIE_VIREMENT$m_oDEI_Montant$m_txtMontant'] = unicode(transfer.amount)
        form['__EVENTTARGET'] = 'MM$VIREMENT$m_WizardBar$m_lnkNext$m_lnkButton'
        if transfer.exec_date != datetime.today().date():
            form['MM$VIREMENT$SAISIE_VIREMENT$virement'] = 'rbDiffere'
            form['MM$VIREMENT$SAISIE_VIREMENT$m_DateDiffere$JJ'] = transfer.exec_date.strftime('%d')
            form['MM$VIREMENT$SAISIE_VIREMENT$m_DateDiffere$MM'] = transfer.exec_date.strftime('%m')
            form['MM$VIREMENT$SAISIE_VIREMENT$m_DateDiffere$AA'] = transfer.exec_date.strftime('%y')
        form.submit()

    def go_add_recipient(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$VIREMENT$SAISIE_VIREMENT$ddlCompteCrediterPro'
        form['MM$VIREMENT$SAISIE_VIREMENT$ddlCompteCrediterPro'] = 'AC'
        form.submit()

    @method
    class iter_emitters(MyEmitters):

        class Item(MyEmitter):

            def obj_label(self):
                """
                Label looks like 'JEAN DUPONT - C.PROF. - 19XXX65 - Solde : 187,12 EUR'.
                We only keep the first half (name and account name).
                What's left is: 'JEAN DUPONT - C.PROF.'
                """
                raw_string = CleanText('.')(self)
                if '-' in raw_string:
                    return '-'.join(raw_string.split('-')[0:2])
                return raw_string

            def obj_balance(self):
                balance_data = CleanText('.')(self).split('Solde')[-1]
                return CleanDecimal().French().filter(balance_data)


class CanceledAuth(Exception):
    pass


class AppValidationPage(LoggedPage, XMLPage):
    def get_status(self):
        return CleanText('//response/status')(self.doc)


class SmsPage(LoggedPage, HTMLPage):
    def on_load(self):
        error = CleanText('//p[@class="warning_trials_before"]')(self.doc)
        if error:
            raise AddRecipientBankError(message='Wrongcode, ' + error)

    def get_prompt_text(self):
        return CleanText('//td[@class="auth_info_prompt"]')(self.doc)

    def post_form(self):
        form = self.get_form(name='downloadAuthForm')
        form.submit()

    def check_canceled_auth(self):
        form = self.doc.xpath('//form[@name="downloadAuthForm"]')
        if form:
            self.location('/Pages/Logout.aspx')
            raise CanceledAuth()

    def set_browser_form(self):
        form = self.get_form(name='formAuth')
        self.browser.recipient_form = dict((k, v) for k, v in form.items() if v)
        self.browser.recipient_form['url'] = form.url


class AuthentPage(LoggedPage, HTMLPage):
    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Authentification réussie")]')(self.doc))

    def go_on(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$RETOUR_OK_SOL$m_ChoiceBar$lnkRight'
        form.submit()


class RecipientPage(LoggedPage, HTMLPage):
    EVENTTARGET = 'MM$WIZARD_AJOUT_COMPTE_EXTERNE'
    FORM_FIELD_ADD = 'MM$WIZARD_AJOUT_COMPTE_EXTERNE$COMPTE_EXTERNE_ADD'

    def on_load(self):
        error = CleanText('//span[@id="MM_LblMessagePopinError"]')(self.doc)
        if error:
            raise AddRecipientBankError(message=error)

    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Ajouter un compte bénéficiaire")] |\
                                //h2[contains(text(), "Confirmer l\'ajout d\'un compte bénéficiaire")]')(self.doc))

    def post_recipient(self, recipient):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = '%s$m_WizardBar$m_lnkNext$m_lnkButton' % self.EVENTTARGET
        form['%s$m_RibIban$txtTitulaireCompte' % self.FORM_FIELD_ADD] = recipient.label
        for i in range(len(recipient.iban) // 4 + 1):
            form['%s$m_RibIban$txtIban%s' % (self.FORM_FIELD_ADD, str(i + 1))] = recipient.iban[4 * i:4 * i + 4]
        form.submit()

    def confirm_recipient(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$WIZARD_AJOUT_COMPTE_EXTERNE$m_WizardBar$m_lnkNext$m_lnkButton'
        form.submit()


class ProAddRecipientOtpPage(IndexPage):
    def on_load(self):
        error = CleanText('//div[@id="MM_m_CH_ValidationSummary" and @class="MessageErreur"]')(self.doc)
        if error:
            raise AddRecipientBankError(message='Wrongcode, ' + error)

    def is_here(self):
        return self.need_auth() and self.doc.xpath('//span[@id="MM_ANR_WS_AUTHENT_ANR_WS_AUTHENT_SAISIE_lblProcedure1"]')

    def set_browser_form(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = 'MM$ANR_WS_AUTHENT$m_WizardBar$m_lnkNext$m_lnkButton'
        self.browser.recipient_form = dict((k, v) for k, v in form.items())
        self.browser.recipient_form['url'] = form.url

    def get_prompt_text(self):
        return CleanText('////span[@id="MM_ANR_WS_AUTHENT_ANR_WS_AUTHENT_SAISIE_lblProcedure1"]')(self.doc)


class ProAddRecipientPage(RecipientPage):
    EVENTTARGET = 'MM$WIZARD_AJOUT_COMPTE_TIERS'
    FORM_FIELD_ADD = 'MM$WIZARD_AJOUT_COMPTE_TIERS$COMPTES_TIERS_ADD'

    def is_here(self):
        return CleanText('//span[@id="MM_m_CH_lblTitle" and contains(text(), "Ajoutez un compte tiers")] |\
                          //span[@id="MM_m_CH_lblTitle" and contains(text(), "Confirmez votre ajout")]')(self.doc)


class TransactionsDetailsPage(LoggedPage, HTMLPage):

    def is_here(self):
        return bool(CleanText('//h2[contains(text(), "Débits différés imputés")] | //span[@id="MM_m_CH_lblTitle" and contains(text(), "Débit différé imputé")]')(self.doc))

    @pagination
    @method
    class get_detail(TableElement):
        item_xpath = '//table[@id="MM_ECRITURE_GLOBALE_m_ExDGEcriture"]/tr[not(@class)] | //table[has-class("small special")]//tbody/tr[@class="rowClick"]'
        head_xpath = '//table[@id="MM_ECRITURE_GLOBALE_m_ExDGEcriture"]/tr[@class="DataGridHeader"]/td | //table[has-class("small special")]//thead/tr/th'

        col_date = 'Date'
        col_label = ['Opération', 'Libellé']
        col_debit = 'Débit'
        col_credit = 'Crédit'

        def next_page(self):
            # only for new website, don't have any accounts with enough deferred card transactions on old webiste
            if self.page.doc.xpath('//a[contains(@id, "lnkSuivante") and not(contains(@disabled,"disabled")) \
                                    and not(contains(@class, "aspNetDisabled"))]'):
                form = self.page.get_form(id='main')
                form['__EVENTTARGET'] = "MM$ECRITURE_GLOBALE$lnkSuivante"
                form['__EVENTARGUMENT'] = ''
                fix_form(form)
                return form.request
            return

        class item(ItemElement):
            klass = Transaction

            obj_raw = Transaction.Raw(TableCell('label'))
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj__debit = CleanDecimal(TableCell('debit'), replace_dots=True, default=0)
            obj__credit = CleanDecimal(TableCell('credit'), replace_dots=True, default=0)

            def obj_amount(self):
                return abs(Field('_credit')(self)) - abs(Field('_debit')(self))

    def go_form_to_summary(self):
        # return to first page
        to_history = Link(self.doc.xpath('//a[contains(text(), "Retour à l\'historique")]'))(self.doc)
        n = re.match('.*\([\'\"](MM\$.*?)[\'\"],.*\)$', to_history)
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = n.group(1)
        form.submit()

    def go_newsite_back_to_summary(self):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = "MM$ECRITURE_GLOBALE$lnkRetourHisto"
        form.submit()


class SubscriptionPage(LoggedPage, HTMLPage):
    def is_here(self):
        return self.doc.xpath('//h2[text()="e-Documents"]') or self.doc.xpath('//h2[text()="Relevés en ligne"]')

    def has_subscriptions(self):
        # This message appears if the customer has not activated the e-Documents yet
        return not bool(self.doc.xpath('//a[contains(text(), "Je souscris au service e-Documents")]'))

    @method
    class iter_subscription(ListElement):
        item_xpath = '//span[@id="MM_CONSULTATION_MULTI_UNIVERS_EDOCUMENTS_ucUniversComptes"]//h3'

        class item(ItemElement):
            klass = Subscription

            obj_id = CleanDecimal('.')
            obj_label = Regexp(CleanText('.'), r'([^\d]*) ')
            obj_subscriber = Field('label')

            def condition(self):
                return bool(CleanDecimal('.', default=NotAvailable)(self))

    @method
    class iter_documents(ListElement):
        # sometimes there is several documents with same label at same date and with same content
        ignore_duplicate = True

        @property
        def item_xpath(self):
            if Env('has_subscription')(self):
                return '//h3[contains(text(), "%s")]//following-sibling::div[@class="panel"][1]/table/tbody/tr' % Env('sub_id')(self)
            return '//div[@id="MM_CONSULTATION_RELEVES_COURRIERS_EDOCUMENTS_divRelevesCourriers"]/table/tbody/tr'

        class item(ItemElement):
            klass = Document

            obj_format = 'pdf'
            obj_url = Regexp(Link('.//td[@class="telecharger"]//a'), r'WebForm_PostBackOptions\("(\S*)"')
            obj_id = Format('%s_%s_%s', Env('sub_id'), CleanText('./td[2]', symbols='/', replace=[(' ', '_')]), Regexp(CleanText('./td[3]'), r'([\wé]*)'))
            obj_label = Format('%s %s', CleanText('./td[3]'), CleanText('./td[2]'))
            obj_date = Date(CleanText('./td[2]'), dayfirst=True)

            def obj_type(self):
                if 'Relevé' in CleanText('./td[3]')(self):
                    return DocumentTypes.STATEMENT
                return DocumentTypes.OTHER

    def download_document(self, document):
        form = self.get_form(id='main')
        form['__EVENTTARGET'] = document.url
        return form.submit()


class UnavailablePage(LoggedPage, HTMLPage):
    # This page seems to not be a 'LoggedPage'
    # but it also is a redirection page from a 'LoggedPage'
    # when the required page is not unavailable
    # so it can also redirect to a 'LoggedPage' page
    pass


class CreditCooperatifMarketPage(LoggedPage, HTMLPage):
    # Stay logged when landing on the new Linebourse
    # (which is used by Credit Cooperatif's connections)
    # The parsing is done in linebourse.api.pages
    def is_error(self):
        return CleanText('//caption[contains(text(),"Erreur")]')(self.doc)
