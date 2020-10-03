# -*- coding: utf-8 -*-

# Copyright(C) 2013      Laurent Bachelier
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

from logging import error
import re
from io import BytesIO

from weboob.browser.pages import HTMLPage, LoggedPage
from weboob.browser.elements import ListElement, ItemElement, method
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Date,
    Env, Regexp, Field, Format,
)
from weboob.browser.filters.html import Attr, Link
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.capabilities.profile import Person
from weboob.capabilities.bill import Document, Subscription, DocumentTypes
from weboob.exceptions import ActionNeeded, BrowserIncorrectPassword, BrowserUnavailable
from weboob.tools.json import json
from weboob.capabilities.base import NotAvailable

from ..captcha import Captcha, TileError
from ..pages.login import LoginPage as LoginParPage, PasswordPage


class Transaction(FrenchTransaction):
    PATTERNS = [
        (
            re.compile(
                r'^CARTE \w+ RETRAIT DAB.*? (?P<dd>\d{2})/(?P<mm>\d{2})( (?P<HH>\d+)H(?P<MM>\d+))? (?P<text>.*)'
            ),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^CARTE \w+ (?P<dd>\d{2})/(?P<mm>\d{2})( A (?P<HH>\d+)H(?P<MM>\d+))? RETRAIT DAB (?P<text>.*)'),
            FrenchTransaction.TYPE_WITHDRAWAL,
        ),
        (
            re.compile(r'^CARTE \w+ REMBT (?P<dd>\d{2})/(?P<mm>\d{2})( A (?P<HH>\d+)H(?P<MM>\d+))? (?P<text>.*)'),
            FrenchTransaction.TYPE_PAYBACK,
        ),
        (re.compile(r'^DEBIT MENSUEL CARTE.*'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^CREDIT MENSUEL CARTE.*'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (
            re.compile(r'^(?P<category>CARTE) \w+ (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<yy>\d{4})\/(?P<mm>\d{2})(?P<dd>\d{2})\d{4}?$'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<dd>\d{2})(?P<mm>\d{2})/(?P<text>.*?)/?(-[\d,]+)?$'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^REMISE CB /(?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*?)/?(-[\d,]+)?$'),
            FrenchTransaction.TYPE_CARD,
        ),
        (
            re.compile(r'^(?P<category>(COTISATION|PRELEVEMENT|TELEREGLEMENT|TIP|PRLV)) (?P<text>.*)'),
            FrenchTransaction.TYPE_ORDER,
        ),
        (
            re.compile(r'^(\d+ )?VIR (PERM )?POUR: (.*?) (REF: \d+ )?MOTIF: (?P<text>.*)'),
            FrenchTransaction.TYPE_TRANSFER,
        ),
        (re.compile(r'^(?P<category>VIR(EMEN)?T? \w+) (?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^(CHEQUE) (?P<text>.*)'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(FRAIS) (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r"^(REGULARISATION DE )?COMMISSION"), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(?P<category>ECHEANCEPRET)(?P<text>.*)'), FrenchTransaction.TYPE_LOAN_PAYMENT),
        (re.compile(r'^(?P<category>REMISE CHEQUES)(?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^CARTE RETRAIT (?P<text>.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
    ]
    _coming = False


class SGPEPage(HTMLPage):
    def get_error(self):
        err = (
            self.doc.xpath('//div[has-class("ngo_mire_reco_message")]')
            or self.doc.xpath('//*[@id="#nge_zone_centre"]//*[has-class("nge_cadre_message_utilisateur")]')
            or self.doc.xpath(u'//div[contains(text(), "Echec de connexion à l\'espace Entreprises")]')
            or self.doc.xpath(u'//div[contains(@class, "waitAuthJetonMsg")]')
        )
        if err:
            return err[0].text.strip()


class ChangePassPage(SGPEPage):
    def on_load(self):
        message = (
            CleanText('//div[@class="ngo_gao_message_intro"]')(self.doc)
            or CleanText('//div[@class="ngo_gao_intro"]')(self.doc)
            or u'Informations manquantes sur le site Société Générale'
        )
        raise ActionNeeded(message)


class LoginEntPage(SGPEPage, PasswordPage):
    """
    be carefull : those differents methods and PREFIX_URL are used
    in another page of an another module which is an abstract of this page
    """
    PREFIX_URL = '/sec'

    @property
    def logged(self):
        return self.doc.xpath('//a[text()="Déconnexion" and @href="/logout"]')

    def get_url(self, path):
        return (self.browser.BASEURL + self.PREFIX_URL + path)

    def get_keyboard_infos(self):
        url = self.get_url('/vk/gen_crypto?estSession=0')
        infos_data = self.browser.open(url).text
        infos_data = re.match(r'^_vkCallback\((.*)\);$', infos_data).group(1)
        infos = json.loads(infos_data.replace("'", '"'))
        return infos

    def get_keyboard_data(self):
        infos = self.get_keyboard_infos()
        infos['grid'] = self.get_grid_data(infos)
        url = self.get_url('/vk/gen_ui?modeClavier=0&cryptogramme=' + infos['crypto'])
        img = Captcha(BytesIO(self.browser.open(url).content), infos)

        try:
            img.build_tiles()
        except TileError as err:
            error("Error: %s" % err)
            if err.tile:
                err.tile.display()

        return {
            'infos': infos,
            'img': img,
        }

    def get_grid_data(self, infos):
        # For ent the grid is already decoded
        return infos['grid']

    def get_authentication_url(self):
        return self.browser.absurl('/authent.html')

    def get_authentication_data(self, login, password):
        keyboard_data = self.get_keyboard_data()
        return {
            'user_id': login,
            'codsec': keyboard_data['img'].get_codes(password[:6]),
            'cryptocvcs': keyboard_data['infos']['crypto'],
            'vk_op': 'auth',
        }

    def login(self, login, password):
        self.browser.location(
            self.get_authentication_url(),
            data=self.get_authentication_data(login, password)
        )


class MainProPage(LoginEntPage):
    def get_grid_data(self, infos):
        # The grid are in b64
        return self.decode_grid(infos)

    def get_authentication_url(self):
        return self.browser.absurl('/sec/vk/authent.json')

    def login(self, login, password):
        authentication_data = self.get_authentication_data(login, password)
        authentication_data.update({
            'top_code_etoile': 0,
            'top_ident': 0,
            'cible': 300,
        })
        self.browser.location(
            self.get_authentication_url(),
            data=authentication_data
        )


class LoginProPage(LoginParPage):
    pass


class CardsPage(LoggedPage, SGPEPage):
    def get_coming_list(self):
        coming_list = []
        for a in self.doc.xpath('//a[contains(@onclick, "changeCarte")]'):
            m = re.findall("'([^']+)'", Attr(a.xpath('.'), 'onclick')(self))
            params = {}
            params['carte'] = m[1]
            params['date'] = m[2]
            coming_list.append(params)
        return coming_list


class CardHistoryPage(LoggedPage, SGPEPage):
    @method
    class iter_transactions(ListElement):
        item_xpath = '//table[@id="tab-corps"]//tr'

        class item(ItemElement):
            klass = Transaction

            obj_rdate = Date(CleanText('./td[1]'), dayfirst=True)
            obj_date = Date(Env('date'), dayfirst=True, default=NotAvailable)
            obj_raw = Transaction.Raw(CleanText('./td[2]'))
            obj__coming = True

            def obj_type(self):
                return Transaction.TYPE_DEFERRED_CARD

            def obj_amount(self):
                return (
                    CleanDecimal('./td[3]', replace_dots=True, default=NotAvailable)(self)
                    or CleanDecimal('./td[2]', replace_dots=True)(self)
                )

            def condition(self):
                return CleanText('./td[2]')(self)

    def has_next(self):
        current = None
        total = None
        for script in self.doc.xpath('//script'):
            if script.text is None:
                continue

            m = re.search(r'var pageActive\s+= (\d+)', script.text)
            if m:
                current = int(m.group(1))
            m = re.search(r"var nombrePage\s+= (\d+)", script.text)
            if m:
                total = int(m.group(1))

        if all((current, total)) and current < total:
            return True

        return False


class ProfileEntPage(LoggedPage, SGPEPage):
    @method
    class get_profile(ItemElement):
        klass = Person

        obj_email = CleanText('//tr[th[text()="Adresse e-mail"]]/td')
        obj_job = CleanText('//tr[th[text()="Fonction dans l\'entreprise"]]/td')
        obj_company_name = CleanText('//tr[th[text()="Raison sociale"]]/td')

        def obj_phone(self):
            return (
                CleanText('//tr[th[contains(text(), "Téléphone mobile")]]/td')(self)
                or CleanText('//tr[th[contains(text(), "Téléphone fixe")]]/td')(self)
                or NotAvailable
            )

        def obj_name(self):
            civility = CleanText('//tr[th[contains(text(), "Civilité")]]/td')(self)
            firstname = CleanText('//tr[th[contains(text(), "Prénom")]]/td')(self)
            lastname = CleanText('//tr[th[contains(text(), "Nom")]]/td')(self)
            return "%s %s %s" % (civility, firstname, lastname)


class SubscriptionPage(LoggedPage, SGPEPage):
    def iter_subscription(self):
        for account in self.doc.xpath('//select[@name="compteSelected"]/option'):
            s = Subscription()
            s.id = CleanText('.', replace=[(' ', '')])(account)

            yield s

    @method
    class iter_documents(ListElement):
        item_xpath = '//table[@id="tab-arretes"]/tbody/tr[td[@class="foncel1-grand"]]'

        class item(ItemElement):
            klass = Document

            obj_label = CleanText('./td[1]')
            obj_date = Date(Regexp(Field('label'), r'au (\d{4}\-\d{2}\-\d{2})'))
            obj_id = Format(
                '%s_%s',
                Env('sub_id'),
                CleanText(Regexp(Field('label'), r'au (\d{4}\-\d{2}\-\d{2})'), replace=[('-', '')])
            )
            obj_format = 'pdf'
            obj_type = DocumentTypes.STATEMENT
            obj_url = Format(
                '/Pgn/PrintServlet?PageID=ReleveRIE&MenuID=BANRELRIE&urlTypeTransfert=ipdf&REPORTNAME=ReleveInteretElectronique.sgi&numeroRie=%s',
                Regexp(Attr('./td[2]/a', 'onclick'), r"impression\('(.*)'\);")
            )


class IncorrectLoginPage(SGPEPage):
    def on_load(self):
        if self.doc.xpath('//div[@class="ngo_mu_message" and contains(text(), "saisies sont incorrectes")]'):
            raise BrowserIncorrectPassword(CleanText('//div[@class="ngo_mu_message"]')(self.doc))


class ErrorPage(SGPEPage):
    def on_load(self):
        if self.doc.xpath('//div[@class="ngo_mu_message" and contains(text(), "momentanément indisponible")]'):
            # Warning: it could occurs because of wrongpass, user have to change password
            raise BrowserUnavailable(CleanText('//div[@class="ngo_mu_message"]')(self.doc))


class InscriptionPage(SGPEPage):
    def get_error(self):
        message = CleanText('//head/title')(self.doc)
        return message


class UselessPage(LoggedPage, SGPEPage):
    pass


class MainPage(LoggedPage, SGPEPage):
    def get_market_accounts_link(self):
        # this is for "ent" website, don't know if it works like "pro" website
        market_accounts_link = Link('//li/a[@title="Comptes-titres"]', default=None)(self.doc)

        if market_accounts_link:
            return market_accounts_link
        elif self.doc.xpath('//span[contains(text(), "Comptes-titres") and contains(@title, "pas habilité à utiliser ce service")]'):
            return NotAvailable
        # return None when we don't know if there are market accounts or not
        # it will be handled in `browser.py`
