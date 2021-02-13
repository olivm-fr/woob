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

from __future__ import unicode_literals

import ast

from collections import OrderedDict
from decimal import Decimal
from io import BytesIO
from datetime import date as da
from lxml import html
import re

from weboob.browser.pages import HTMLPage, LoggedPage, JsonPage, PartialHTMLPage
from weboob.browser.elements import method, ItemElement, TableElement
from weboob.browser.filters.standard import CleanText, Date, CleanDecimal, Regexp, Format, Field, Eval, Lower
from weboob.browser.filters.json import Dict
from weboob.browser.filters.html import Attr, TableCell
from weboob.exceptions import ActionNeeded, BrowserUnavailable, BrowserPasswordExpired
from weboob.capabilities.bank import Account, AccountOwnership
from weboob.capabilities.wealth import Investment
from weboob.capabilities.profile import Profile
from weboob.capabilities.base import Currency, find_object
from weboob.capabilities import NotAvailable
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.capabilities.bank.investments import is_isin_valid
from weboob.tools.captcha.virtkeyboard import GridVirtKeyboard
from weboob.tools.compat import quote, unicode
from weboob.tools.misc import to_unicode
from weboob.tools.json import json


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True, default=NotAvailable)
    return CleanDecimal(*args, **kwargs)

def MyStrip(x, xpath='.'):
    if isinstance(x, unicode):
        return CleanText(xpath)(html.fromstring("<p>%s</p>" % x))
    elif isinstance(x, bytes):
        x = x.decode('utf-8')
        return CleanText(xpath)(html.fromstring("<p>%s</p>" % x))
    else:
        return CleanText(xpath)(html.fromstring(CleanText('.')(x)))


class CDNVirtKeyboard(GridVirtKeyboard):
    symbols = {'0': '3de2346a63b658c977fce4da925ded28',
               '1': 'c571018d2dc267cdf72fafeeb9693037',
               '2': '72d7bad4beb833d85047f6912ed42b1d',
               '3': 'fbfce4677a8b2f31f3724143531079e3',
               '4': '54c723c5b0b5848a0475b4784100b9e0',
               '5': 'd00164307cacd4ca21b930db09403baa',
               '6': '101adc6f5d03df0f512c3ec2bef88de9',
               '7': '3b48f598209718397eb1118d81cf07ba',
               '8': '881f0acdaba2c44b6a5e64331f4f53d3',
               '9': 'a47d9a0a2ebbc65a0e625f20cb07822b',
              }

    margin = 1
    color = (0xff,0xf7,0xff)
    nrow = 4
    ncol = 4

    def __init__(self, browser, crypto, grid):
        f = BytesIO(browser.open('/sec/vk/gen_ui?modeClavier=0&cryptogramme=%s' % crypto).content)

        super(CDNVirtKeyboard, self).__init__(range(16), self.ncol, self.nrow, f, self.color)
        self.check_symbols(self.symbols, browser.responses_dirname)
        self.codes = grid

    def check_color(self, pixel):
        for p in pixel:
            if p > 0xd0:
                return False
        return True

    def get_string_code(self, string):
        res = []
        ndata = self.nrow * self.ncol
        for nbchar, c in enumerate(string):
            index = self.get_symbol_code(self.symbols[c])
            res.append(self.codes[(nbchar * ndata) + index])
        return ','.join(map(str, res))


class HTMLErrorPage(HTMLPage):
    def get_error(self):
        # No Coalesce here as both can be empty
        return CleanText('//b[has-class("x-attentionErreurLigneHaut")]')(self.doc) or \
               CleanText('//div[has-class("x-attentionErreur")]/b')(self.doc)


class RedirectPage(HTMLPage):
    def on_load(self):
        link = Regexp(CleanText('//script'), 'href="(.*)"', default='')(self.doc)
        if link:
            self.browser.location(link)


class EntryPage(LoggedPage, HTMLErrorPage):
    pass


class LoginConfirmPage(JsonPage):
    def get_reason(self):
        return Dict('commun/raison', default='')(self.doc)

    def get_status(self):
        return Dict('commun/statut')(self.doc)


class LoginPage(HTMLErrorPage):
    VIRTUALKEYBOARD = CDNVirtKeyboard

    def login(self, username, password):
        res = self.browser.open('/sec/vk/gen_crypto.json').json()
        crypto = res['donnees']['crypto']
        grid = res['donnees']['grid']

        vk = self.VIRTUALKEYBOARD(self.browser, crypto, grid)

        data = {
            'user_id': username,
            'vk_op': 'auth',
            'codsec': vk.get_string_code(password),
            'cryptocvcs': crypto,
        }
        self.browser.location('/sec/vk/authent.json', data=data)

    def classic_login(self, username, password):
        m = re.match('https://www.([^\.]+).fr', self.browser.BASEURL)
        if not m:
            bank_name = 'credit-du-nord'
            self.logger.error('Unable to find bank name for %s' % self.browser.BASEURL)
        else:
            bank_name = m.group(1)

        data = {'bank':         bank_name,
                'pagecible':    'vos-comptes',
                'password':     password.encode(self.browser.ENCODING),
                'pwAuth':       'Authentification+mot+de+passe',
                'username':     username.encode(self.browser.ENCODING),
               }
        self.browser.location('/saga/authentification', data=data)


class AccountTypePage(LoggedPage, JsonPage):
    def get_account_type(self):
        account_type = CleanText(Dict('donnees/id'))(self.doc)
        if account_type == "menu_espace_perso_part":
            return "particuliers"
        elif account_type == "menu_espace_perso_pro":
            return "professionnels"
        elif account_type == "menu_espace_perso_ent":
            return "entreprises"

REASONS_MAPPING = {
    'SCA': 'Vous devez réaliser la double authentification sur le portail internet',
    'SCAW': 'Vous devez choisir si vous souhaitez dès à présent activer la double authentification sur le portail internet',
    'GDPR': 'GDPR',
    'alerting_pull_incitation': 'Mise à jour de votre dossier',  # happens when the user needs to send a document ID
}


class LabelsPage(LoggedPage, JsonPage):
    def on_load(self):
        if Dict('commun/statut', default='')(self.doc) == 'nok':
            reason = Dict('commun/raison')(self.doc)
            assert reason in REASONS_MAPPING, 'Labels page is not available with message %s' % reason
            raise ActionNeeded(REASONS_MAPPING[reason])

    def get_labels(self):
        synthesis_labels = ["synthèse"]
        loan_labels = ["crédits en cours", "crédits perso et immo", "crédits", "crédits personnels et immobiliers"]
        loan_label = "CREDITS"
        keys = [key for key in Dict('donnees')(self.doc) if key.get('label').lower() in ['crédits', 'comptes et cartes']]
        for key in keys:
            for element in Dict('submenu')(key):
                if Lower(CleanText(Dict('label')))(element) in synthesis_labels:
                    synthesis_label = CleanText(Dict('link'))(element).split("/")[-1]
                if Lower(CleanText(Dict('label')))(element) in loan_labels:
                    loan_label = CleanText(Dict('link'))(element).split("/")[-1]
        return (synthesis_label, loan_label)


class ProfilePage(LoggedPage, JsonPage):
    def get_profile(self):
        if CleanText(Dict('commun/statut', default=''))(self.doc) == 'nok':
            reason = CleanText(Dict('commun/raison', default=''))(self.doc)
            assert reason in REASONS_MAPPING, 'Unhandled error : %s' % reason
            raise ActionNeeded(REASONS_MAPPING[reason])

        profile = Profile()
        profile.name = Format('%s %s', CleanText(Dict('donnees/nom')), CleanText(Dict('donnees/prenom'), default=''))(self.doc)
        return profile


class CDNBasePage(HTMLPage):
    def get_from_js(self, pattern, end_pattern, is_list=False):
        """
        find a pattern in any javascript text
        """
        for script in self.doc.xpath('//script'):
            txt = script.text
            if txt is None:
                continue

            start = txt.find(pattern)
            if start < 0:
                continue

            values = []
            while start >= 0:
                start += len(pattern)
                end = txt.find(end_pattern, start)
                values.append(txt[start:end])

                if not is_list:
                    break

                start = txt.find(pattern, end)
            return ','.join(values)

    def get_execution(self):
        return self.get_from_js("name: 'execution', value: '", "'")

    def iban_go(self):
        value_from_js = self.get_from_js('C_PROXY.StaticResourceClientTranslation( "', '"')
        if not value_from_js:
            return None
        return '/vos-comptes/IPT/cdnProxyResource%s' % value_from_js


class ProIbanPage(CDNBasePage):
    pass


class AVPage(LoggedPage, CDNBasePage):
    COL_LABEL = 0
    COL_BALANCE = 3

    ARGS = ['IndiceClassement', 'IndiceCompte', 'Banque', 'Agence', 'Classement', 'Serie', 'SScompte', 'Categorie', 'IndiceSupport', 'NumPolice', 'LinkHypertext']

    def get_params(self, text):
        url = self.get_from_js('document.detail.action="', '";')
        args = {}
        l = []
        for sub in re.findall("'([^']*)'", text):
            l.append(sub)
        for i, key in enumerate(self.ARGS):
            args[key] = l[self.ARGS.index(key)]
        return url, args

    def get_av_accounts(self):
        for table in self.doc.xpath('//table[@class="datas"]'):
            head_cols = table.xpath('./tr[@class="entete"]/td')
            for tr in table.xpath('./tr[not(@class)]'):
                cols = tr.findall('td')
                if len(cols) != 4:
                    continue

                a = Account()

                # get acc_nb like on accounts page
                a.number = a._acc_nb = Regexp(
                    CleanText('//div[@id="v1-cadre"]//b[contains(text(), "Compte N")]', replace=[(' ', '')]),
                    r'(\d+)'
                )(self.doc)[5:]

                a.label = CleanText('.')(cols[self.COL_LABEL])
                a.type = Account.TYPE_LIFE_INSURANCE
                a.balance = MyDecimal('.')(cols[self.COL_BALANCE])
                a.currency = a.get_currency(CleanText('.')(head_cols[self.COL_BALANCE]))
                a._link, a._args = self.get_params(cols[self.COL_LABEL].find('span/a').attrib['href'])
                a.id = '%s%s%s' % (a._acc_nb, a._args['IndiceSupport'], a._args['NumPolice'])
                a._inv = True
                yield a


class PartAVPage(AVPage):
    pass


class AccountsPageMixin(LoggedPage, CDNBasePage):
    COL_HISTORY = 2
    COL_FIRE_EVENT = 3
    COL_LABEL = 5

    TYPES = {
        'CARTE':                   Account.TYPE_CARD,
        'COMPTE COURANT':          Account.TYPE_CHECKING,
        'CPTE EXPLOITATION IMMOB': Account.TYPE_CHECKING,
        'CPT COURANT':             Account.TYPE_CHECKING,
        'CONSEILLE RESIDENT':      Account.TYPE_CHECKING,
        'PEA':                     Account.TYPE_PEA,
        'P.E.A':                   Account.TYPE_PEA,
        'COMPTE ÉPARGNE':          Account.TYPE_SAVINGS,
        'COMPTE EPARGNE':          Account.TYPE_SAVINGS,
        'COMPTE SUR LIVRET':       Account.TYPE_SAVINGS,
        'LDDS':                    Account.TYPE_SAVINGS,
        'LIVRET':                  Account.TYPE_SAVINGS,
        "PLAN D'EPARGNE":          Account.TYPE_SAVINGS,
        'PLAN ÉPARGNE':            Account.TYPE_SAVINGS,
        'ASS.VIE':                 Account.TYPE_LIFE_INSURANCE,
        'BONS CAPI':               Account.TYPE_CAPITALISATION,
        'ÉTOILE AVANCE':           Account.TYPE_LOAN,
        'ETOILE AVANCE':           Account.TYPE_LOAN,
        'PRÊT':                    Account.TYPE_LOAN,
        'CREDIT':                  Account.TYPE_LOAN,
        'FACILINVEST':             Account.TYPE_LOAN,
        'COMPTE TIT':              Account.TYPE_MARKET,
        'PRDTS BLOQ. TIT':         Account.TYPE_MARKET,
        'PRODUIT BLOQUE TIT':      Account.TYPE_MARKET,
        'COMPTE A TERME':          Account.TYPE_DEPOSIT,
    }

    def get_account_type(self, label):
        # To differenciate between 'COMPTE COURANT' & 'COMPTE COURANT TITRES',
        # in the TYPES dictionary, we type Market accounts right away
        if 'TITRES' in label:
            return Account.TYPE_MARKET
        for pattern, actype in sorted(self.TYPES.items()):
            if label.startswith(pattern) or label.endswith(pattern):
                return actype
        return Account.TYPE_UNKNOWN


class AccountsPage(AccountsPageMixin):
    COL_ID = 4
    COL_BALANCE = -1

    def make__args_dict(self, line):
        return {
            '_eventId': 'clicDetailCompte',
            '_ipc_eventValue': '',
            '_ipc_fireEvent': '',
            'execution': self.get_execution(),
            'idCompteClique': line[self.COL_ID],
        }

    def get_password_expired(self):
        error = CleanText('//div[@class="x-attentionErreur"]/b')(self.doc)
        if "vous devez modifier votre code confidentiel à la première connexion" in error:
            return error

    def get_history_link(self):
        return CleanText().filter(self.get_from_js(",url: Ext.util.Format.htmlDecode('", "'")).replace('&amp;', '&')

    def get_account_ownership(self, owner_pos, acc_id, name):
        acc_id_pos = self.text.find(acc_id)
        reg = re.compile(r'(m|mr|me|mme|mlle|mle|ml)\.? (.*)\bet (m|mr|me|mme|mlle|mle|ml)\b(.*)', re.IGNORECASE)
        for pos, owner in owner_pos.items():
            if acc_id_pos < pos:
                if reg.search(owner):
                    return AccountOwnership.CO_OWNER
                elif all(n in owner.upper() for n in name.split()):
                    return AccountOwnership.OWNER
                return AccountOwnership.ATTORNEY

    def get_list(self, name):
        accounts = []
        previous_account = None

        noaccounts = self.get_from_js('_js_noMvts =', ';')
        if noaccounts is not None:
            assert 'avez aucun compte' in noaccounts
            return []

        txt = self.get_from_js('_data = new Array(', ');', is_list=True)

        if txt is None:
            raise BrowserUnavailable('Unable to find accounts list in scripts')

        owner_pos = OrderedDict()
        for m in re.finditer(r'(M\. .*|Mme .*|Mlle .*)(?=\')', self.text):
            owner_pos[m.start()] = m.group(1)

        data = json.loads('[%s]' % txt.replace("'", '"'))

        for line in data:
            a = Account()
            a.id = line[self.COL_ID].replace(' ', '')

            if re.match(r'Classement=(.*?):::Banque=(.*?):::Agence=(.*?):::SScompte=(.*?):::Serie=(.*)', a.id):
                a.id = str(CleanDecimal().filter(a.id))

            idparts = a.id.split('_')
            if len(idparts) > 1:
                a.number = a._acc_nb = idparts[0]
            else:
                a._acc_nb = None

            a.label = MyStrip(line[self.COL_LABEL], xpath='.//div[@class="libelleCompteTDB"]')
            # This account can be multiple life insurance accounts
            if a.label == 'ASSURANCE VIE-BON CAPI-SCPI-DIVERS *':
                continue

            a.ownership = self.get_account_ownership(owner_pos, line[self.COL_ID], name)
            a.balance = Decimal(FrenchTransaction.clean_amount(line[self.COL_BALANCE]))
            a.currency = a.get_currency(line[self.COL_BALANCE])
            a.type = self.get_account_type(a.label)

            # The parent account must be created right before
            if a.type == Account.TYPE_CARD:
                # duplicate
                if find_object(accounts, id=a.id):
                    self.logger.warning('Ignoring duplicate card %r', a.id)
                    continue
                a.parent = previous_account

            if line[self.COL_HISTORY] == 'true':
                a._inv = False
                a._link = self.get_history_link()
                a._args = self.make__args_dict(line)
            else:
                a._inv = True
                a._args = {'_ipc_eventValue':  line[self.COL_ID],
                           '_ipc_fireEvent':   line[self.COL_FIRE_EVENT],
                          }
                #a._link = self.doc.xpath('//form[@name="changePageForm"]')[0].attrib['action']

            if a.type is Account.TYPE_CARD:
                a.coming = a.balance
                a.balance = Decimal('0.0')

            accounts.append(a)
            previous_account = a

        return accounts

    def iban_page(self):
        form = self.get_form(name="changePageForm")
        form['_ipc_fireEvent'] = 'V1_rib'
        form['_ipc_eventValue'] = 'bouchon=bouchon'
        form.submit()

    def get_strid(self):
        return re.search(r'(\d{4,})', Attr('//form[@name="changePageForm"]', 'action')(self.doc)).group(0)


class ProAccountsPage(PartialHTMLPage, AccountsPageMixin):
    COL_ID = 0
    COL_BALANCE = 1

    ARGS = [
        'Banque', 'Agence', 'Classement', 'Serie', 'SSCompte', 'Devise',
        'CodeDeviseCCB', 'LibelleCompte', 'IntituleCompte', 'Indiceclassement',
        'IndiceCompte', 'NomClassement',
    ]

    def on_load(self):
        if self.doc.xpath('//h1[contains(text(), "Erreur")]'):
            raise BrowserUnavailable(CleanText('//h1[contains(text(), "Erreur")]//span')(self.doc))
        msg = CleanText('//div[@class="x-attentionErreur"]/b')(self.doc)
        if 'vous devez modifier votre code confidentiel' in msg:
            raise BrowserPasswordExpired(msg)

    def params_from_js(self, text):
        l = []
        for sub in re.findall("'([^']*)'", text):
            l.append(sub)

        if len(l) <= 1:
            #For account that have no history
            return None, None

        url = '/vos-comptes/IPT/appmanager/transac/' + self.browser.account_type + '?_nfpb=true&_windowLabel=portletInstance_18&_pageLabel=page_synthese_v1' + '&_cdnCltUrl=' + "/transacClippe/" + quote(l.pop(0))
        args = {}
        for input in self.doc.xpath('//form[@name="detail"]/input'):
            args[input.attrib['name']] = input.attrib.get('value', '')

        for i, key in enumerate(self.ARGS):
            args[key] = to_unicode(l[self.ARGS.index(key)])

        args['PageDemandee'] = 1
        args['PagePrecedente'] = 1

        return url, args

    def get_list(self):
        no_accounts_message = self.doc.xpath(u'//span/b[contains(text(),"Votre abonnement est clôturé. Veuillez contacter votre conseiller.")]/text()')
        if no_accounts_message:
            raise ActionNeeded(no_accounts_message[0])

        previous_checking_account = None
        # Several deposit accounts ('Compte à terme') have the same id and the same label
        # So a number is added to distinguish them
        previous_deposit_account = None
        deposit_count = 1
        for tr in self.doc.xpath('//table[has-class("datas")]//tr'):
            if tr.attrib.get('class', '') == 'entete':
                owner = CleanText('.')(tr.findall('td')[0])
                continue

            cols = tr.findall('td')

            a = Account()
            a.label = unicode(cols[self.COL_ID].xpath('.//span[@class="left-underline"] | .//span[@class="left"]/a')[0].text.strip())
            a.type = self.get_account_type(a.label)
            a.ownership = self.get_account_ownership(owner)
            balance = CleanText('.')(cols[self.COL_BALANCE])
            if balance == '':
                continue
            a.balance = CleanDecimal(replace_dots=True).filter(balance)
            a.currency = a.get_currency(balance)
            if cols[self.COL_ID].find('a'):
                a._link, a._args = self.params_from_js(cols[self.COL_ID].find('a').attrib['href'])
            # There may be a href with 'javascript:NoDetail();'
            # The _link and _args should be None
            else:
                a._link, a._args = None, None
            a.number = a._acc_nb = cols[self.COL_ID].xpath('.//span[@class="right-underline"] | .//span[@class="right"]')[0].text.replace(' ', '').strip()

            a.id = a._acc_nb

            # If available we add 'IndiceCompte' and 'IndiceClassement' to the id due to the lack of information
            # on the website. This method is not enough because on some connections, if there are multiple account with the
            # same id and the same label, but with different currencies, we will add an index at the end of the id relative to the
            # order the accounts appear on the website. This will cause the accounts to be shifted when the user will add a new account
            # with same label/id, if this happens the new account will appear first on the website and it will take the index of '1'
            # previously used by the first account. the already gathered transactions of the previously first account will appear on
            # the new first account, the already gathered transactions of the previously second account will appear on the new
            # second account (the previous one), etc.

            if hasattr(a, '_args') and a._args:
                if a._args['IndiceCompte'].isdigit():
                    a.id = '%s%s' % (a.id, a._args['IndiceCompte'])
                if a._args['Indiceclassement'].isdigit():
                    a.id = '%s%s' % (a.id, a._args['Indiceclassement'])

            # This account can be multiple life insurance accounts
            if (any(a.label.startswith(lab) for lab in ['ASS.VIE-BONS CAPI-SCPI-DIVERS', 'BONS CAPI-SCPI-DIVERS'])
                or (u'Aucun d\\351tail correspondant pour ce compte' in tr.xpath('.//a/@href')[0])
                    and 'COMPTE A TERME' not in tr.xpath('.//span[contains(@class, "left")]/text()')[0]):
                continue

            if a.type is Account.TYPE_CARD:
                a.coming = a.balance
                a.balance = Decimal('0.0')

                # Take the predecessiong checking account as parent
                if previous_checking_account:
                    a.parent = previous_checking_account
                else:
                    self.logger.warning('The card account %s has no parent account' % a.id)

            a._inv = True

            if a.type == Account.TYPE_CHECKING:
                previous_checking_account = a

            if previous_deposit_account and previous_deposit_account.id == a.id:
                a.id = a.id + '_%s' % deposit_count
                deposit_count += 1
                previous_deposit_account = a

            if a.type == Account.TYPE_DEPOSIT:
                previous_deposit_account = a

            yield a

    def get_account_ownership(self, owner):
        if re.search(r'(m|mr|me|mme|mlle|mle|ml)\.? (m|mr|me|mme|mlle|mle|ml)\b', owner, re.IGNORECASE):
            return AccountOwnership.CO_OWNER
        return AccountOwnership.OWNER

    def iban_page(self):
        self.browser.location(self.doc.xpath('.//a[contains(text(), "Impression IBAN")]')[0].attrib['href'])

    def has_iban(self):
        return not bool(CleanText('//*[contains(., "pas de compte vous permettant l\'impression de RIB")]')(self.doc))


class IbanPage(LoggedPage, HTMLPage):
    def get_iban(self):
        try:
            return unicode(self.doc.xpath('.//td[@width="315"]/font')[0].text.replace(' ', '').strip())
        except AttributeError:
            return NotAvailable


class Transaction(FrenchTransaction):
    PATTERNS = [(re.compile(r'^(?P<text>RET DAB \w+ .*?) LE (?P<dd>\d{2})(?P<mm>\d{2})$'),
                                                            FrenchTransaction.TYPE_WITHDRAWAL),
                (re.compile(r'^(E-)?VIR(EMENT)?( SEPA)?( INTERNET)?(\.| )?(DE)? (?P<text>.*?)( Motif ?:.*)?$'),
                                                            FrenchTransaction.TYPE_TRANSFER),
                (re.compile(r'^PRLV (SEPA )?(DE )?(?P<text>.*?)( Motif :.*)?$'),
                                                            FrenchTransaction.TYPE_ORDER),
                (re.compile(r'^CB( [0-9]+)? (?P<text>.*) LE (?P<dd>\d{2})\.?(?P<mm>\d{2})$'),
                                                            FrenchTransaction.TYPE_CARD),
                (re.compile(r'^CHEQUE.*'),                  FrenchTransaction.TYPE_CHECK),
                (re.compile(r'^(CONVENTION \d+ )?COTISATION (?P<text>.*)'),
                                                            FrenchTransaction.TYPE_BANK),
                (re.compile(r'^DEP[^ ]* (?P<text>GAB .*?) LE (?P<dd>\d{2}).?(?P<mm>\d{2})$'),  FrenchTransaction.TYPE_DEPOSIT),
                (re.compile(r'^REM(ISE)?\.?( CHE?Q(UE\.)?)? .*'),  FrenchTransaction.TYPE_DEPOSIT),
                (re.compile(r'^(?P<text>.*?)( \d{2}.*)? LE (?P<dd>\d{2})\.?(?P<mm>\d{2})$'),
                                                            FrenchTransaction.TYPE_CARD),
                (re.compile(r'^(?P<text>.*?) LE (?P<dd>\d{2}) (?P<mm>\d{2}) (?P<yy>\d{2})$'),
                                                            FrenchTransaction.TYPE_CARD),
               ]


class TransactionsPage(LoggedPage, CDNBasePage):
    TRANSACTION = Transaction

    COL_ID = 0
    COL_DATE = -5
    COL_DEBIT_DATE = -4
    COL_LABEL = -3
    COL_VALUE = -1

    def on_load(self):
        msg = CleanText('//h1[contains(text(), "Avenant")]')(self.doc)
        if msg:
            raise ActionNeeded(msg)

    def get_next_args(self, args):
        if self.is_last():
            return None

        args['_eventId'] = 'clicChangerPageSuivant'
        args['execution'] = self.get_execution()
        args.pop('idCompteClique', None)
        return args

    def is_last(self):
        for script in self.doc.xpath('//script'):
            txt = script.text
            if txt is None:
                continue

            if txt.find('clicChangerPageSuivant') >= 0:
                return False

        return True

    def condition(self, t, acc_type):
        if t.date is NotAvailable:
            return True

        t._is_coming = (t.date > da.today()) or (t.vdate is NotAvailable)

        if t.raw.startswith('TOTAL DES') or t.raw.startswith('ACHATS CARTE'):
            t.type = t.TYPE_CARD_SUMMARY
        elif acc_type is Account.TYPE_CARD:
            t.type = t.TYPE_DEFERRED_CARD
        return False

    def get_history(self, account):
        txt = self.get_from_js('ListeMvts_data = new Array(', ');\n')
        if txt is None:
            no_trans = self.get_from_js('js_noMvts = new Ext.Panel(', ')')
            if no_trans is not None:
                # there is no transactions for this account, this is normal.
                return
            else:
                # No history on this account
                return

        data = ast.literal_eval('[%s]' % txt.replace('"', '\\"'))

        for line in data:
            t = self.TRANSACTION()

            if account.type is Account.TYPE_CARD and MyStrip(line[self.COL_DEBIT_DATE]):
                date = vdate = Date(dayfirst=True).filter(MyStrip(line[self.COL_DEBIT_DATE]))
                t.bdate = Date(dayfirst=True, default=NotAvailable).filter(MyStrip(line[self.COL_DATE]))
            else:
                date = Date(dayfirst=True, default=NotAvailable).filter(MyStrip(line[self.COL_DATE]))
                if not date:
                    continue
                if MyStrip(line[self.COL_DEBIT_DATE]):
                    vdate = MyStrip(line[self.COL_DEBIT_DATE])
                    if vdate != '':
                        vdate = Date(dayfirst=True).filter(vdate)
                else:
                    vdate = date
            raw = MyStrip(line[self.COL_LABEL])

            t.parse(date, raw, vdate=vdate)
            t.set_amount(line[self.COL_VALUE])

            #if t.amount == 0 and t.label.startswith('FRAIS DE '):
            #    m = re.search(r'(\b\d+,\d+)E\b', t.label)
            #    if m:
            #        t.amount = -CleanDecimal(replace_dots=True).filter(m.group(1))
            #        self.logger.info('parsing amount in transaction label: %r', t)

            if self.condition(t, account.type):
                continue

            yield t

    def can_iter_investments(self):
        return 'Vous ne pouvez pas utiliser les fonctions de bourse.' not in CleanText('//div[@id="contenusavoir"]')(self.doc)

    def not_restrained(self):
        return not CleanText('//div[contains(text(), "restreint aux fonctions de bourse")]')(self.doc)

    @method
    class get_market_investment(TableElement):
        # Fetch the tables with at least 5 head columns (browser adds a missing a <tbody>)
        item_xpath = '//div[not(@id="PortefeuilleCV")]/table[@class="datas"][tr[@class="entete"][count(td)>4]]//tr[position()>1]'
        head_xpath = '//div[not(@id="PortefeuilleCV")]/table[@class="datas"][tr[@class="entete"][count(td)>4]]//tr[@class="entete"]/td'

        col_label = 'Valeur'
        col_quantity = 'Quantité'
        col_unitvalue = 'Cours'
        col_unitprice = 'Prix de revient'
        col_valuation = 'Estimation'
        col_portfolio_share = '%'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label', colspan=True))
            obj_valuation = CleanDecimal.French(TableCell('valuation', colspan=True))
            obj_quantity = CleanDecimal.French(TableCell('quantity', colspan=True), default=NotAvailable)
            obj_unitvalue = CleanDecimal.French(TableCell('unitvalue', colspan=True), default=NotAvailable)
            obj_unitprice = CleanDecimal.French(
                TableCell('unitprice', colspan=True, default=None),
                default=NotAvailable
            )

            def obj_portfolio_share(self):
                portfolio_share_percent = CleanDecimal.French(TableCell('portfolio_share'), default=None)(self)
                if portfolio_share_percent is not None:
                    return portfolio_share_percent / 100
                return NotAvailable

            def obj_code(self):
                for code in Field('label')(self).split():
                    if is_isin_valid(code):
                        return code
                return NotAvailable

            def obj_code_type(self):
                if is_isin_valid(Field('code')(self)):
                    return Investment.CODE_TYPE_ISIN
                return NotAvailable

            def condition(self):
                return "Sous-total" not in Field('label')(self)

    @method
    class get_li_investments(TableElement):
        item_xpath = '//table[@class="datas"]//tr[position()>1]'
        head_xpath = '//table[@class="datas"]//tr[@class="entete"]/td/*'

        col_label = u'Libellé'
        col_quantity = u'Quantité'
        col_unitvalue = re.compile(u"Valeur liquidative")
        col_valuation = re.compile(u"Montant")
        col_portfolio_share = 'Répartition (%)'

        class item(ItemElement):
            klass = Investment
            obj_label = CleanText(TableCell('label'))
            obj_quantity = MyDecimal(CleanText(TableCell('quantity')))
            obj_valuation = MyDecimal(TableCell('valuation'))
            obj_unitvalue = MyDecimal(TableCell('unitvalue'))

            def obj_portfolio_share(self):
                if MyDecimal(TableCell('portfolio_share'), default=None)(self):
                    return Eval(lambda x: x / 100, MyDecimal(TableCell('portfolio_share')))(self)
                return NotAvailable

            def obj_code(self):
                for code in Field('label')(self).split():
                    if is_isin_valid(code):
                        return code
                return NotAvailable

            def obj_vdate(self):
                if Field('unitvalue') is NotAvailable:
                    vdate = Date(dayfirst=True, default=NotAvailable)\
                       .filter(Regexp(CleanText('.'), '(\d{2})/(\d{2})/(\d{4})', '\\3-\\2-\\1', default=NotAvailable)(TableCell('unitvalue')(self))) or \
                       Date(dayfirst=True, default=NotAvailable)\
                       .filter(Regexp(CleanText('//tr[td[span[b[contains(text(), "Estimation du contrat")]]]]/td[2]'),
                                      '(\d{2})/(\d{2})/(\d{4})', '\\3-\\2-\\1', default=NotAvailable)(TableCell('unitvalue')(self)))
                    return vdate

    def fill_diff_currency(self, account):
        valuation_diff = CleanText(u'//td[span[contains(text(), "dont +/- value : ")]]//b', default=None)(self.doc)
        account.balance = CleanDecimal.French(Regexp(CleanText('//table[@class="v1-formbloc"]//td[@class="v1-labels"]//b[contains(text(), "Estimation du contrat")]/ancestor::td/following-sibling::td[1]'), r'^(.+) EUR'))(self.doc)
        # NC == Non communiqué
        if valuation_diff and "NC" not in valuation_diff:
            account.valuation_diff = MyDecimal().filter(valuation_diff)
            account.currency = account.get_currency(valuation_diff)


class ProTransactionsPage(TransactionsPage):
    TRANSACTION = Transaction

    def get_error(self):
        return CleanText('//b[contains(text(), "momentanément indisponible")]')(self.doc)

    def get_next_args(self, args):
        if len(self.doc.xpath('//a[contains(text(), "Suivant")]')) > 0:
            args['PageDemandee'] = int(args.get('PageDemandee', 1)) + 1
            return args

        return None

    def parse_transactions(self):
        transactions = {}
        for script in self.doc.xpath('//script'):
            txt = script.text
            if txt is None:
                continue

            for i, key, value in re.findall('listeopecv\[(\d+)\]\[\'(\w+)\'\]="(.*)";', txt):
                i = int(i)
                if i not in transactions:
                    transactions[i] = {}
                transactions[i][key] = value.strip()

        return sorted(transactions.items())

    # We don't want detect the account_devise as an original_currency, since it's
    # already the main currency
    def detect_currency(self, t, raw, account_devise):
        matches = []
        for currency in Currency.CURRENCIES:
            if currency != account_devise and ' ' + currency + ' ' in raw:
                m = re.search(r'(\d+[,.]\d{1,2}? ' + currency + r')', raw)
                if m:
                    matches.append((m, currency))
        assert len(matches) in [0,1]
        if matches:
            match = matches[0][0]
            currency = matches[0][1]
            t.original_currency = currency
            t.original_amount = abs(MyDecimal().filter(match.group()))
            if (t.amount < 0):
                t.original_amount = -t.original_amount

    def get_history(self, account):
        for i, tr in self.parse_transactions():
            t = self.TRANSACTION()

            if account.type is Account.TYPE_CARD:
                date = vdate = Date(dayfirst=True, default=None).filter(tr['dateval'])
                t.bdate = Date(dayfirst=True, default=NotAvailable).filter(tr['date'])
            else:
                date = Date(dayfirst=True, default=None).filter(tr['date'])
                vdate = Date(dayfirst=True, default=None).filter(tr['dateval']) or date
            raw = MyStrip(' '.join([tr['typeope'], tr['LibComp']]))
            t.parse(date, raw, vdate)
            t.set_amount(tr['mont'])
            self.detect_currency(t, raw, account.currency)

            if self.condition(t, account.type):
                continue

            yield t


class RgpdPage(LoggedPage, CDNBasePage):
    pass
