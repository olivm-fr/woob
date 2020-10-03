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

from binascii import hexlify
import datetime
from decimal import Decimal
import re
import sys
from io import BytesIO
from PIL import Image, ImageFilter

from weboob.browser.elements import method, DictElement, ItemElement
from weboob.browser.filters.standard import (
    CleanText, CleanDecimal, Regexp, Eval,
    Date, Field, MapIn, Coalesce,
)
from weboob.browser.filters.html import Attr, Link, AttributeNotFound
from weboob.browser.filters.json import Dict
from weboob.exceptions import BrowserUnavailable, BrowserIncorrectPassword, ActionNeeded

from weboob.browser.pages import (
    HTMLPage, LoggedPage, FormNotFound, JsonPage, RawPage, XMLPage,
    AbstractPage,
)

from weboob.capabilities.bank import Account
from weboob.capabilities.wealth import Investment
from weboob.capabilities.profile import Person
from weboob.capabilities.contact import Advisor
from weboob.capabilities import NotAvailable
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.capabilities.bank.investments import IsinCode, IsinType
from weboob.tools.captcha.virtkeyboard import SplitKeyboard
from weboob.tools.decorators import retry
from weboob.tools.compat import urlsplit, parse_qsl
from weboob.tools.json import json
from weboob.tools.misc import to_unicode
from weboob.tools.pdf import get_pdf_rows


class LoggedOut(Exception):
    pass


class BrokenPageError(Exception):
    pass


class WikipediaARC4(object):
    def __init__(self, key=None):
        assert isinstance(key, bytes)
        self.state = list(range(256))
        self.x = self.y = 0

        if key is not None:
            self.init(key)

    @staticmethod
    def ord(i):
        if sys.version_info.major < 3:
            return ord(i)
        return i

    @staticmethod
    def chr(i):
        if sys.version_info.major < 3:
            return chr(i)
        return bytes([i])

    def init(self, key):
        for i in range(256):
            self.x = (self.ord(key[i % len(key)]) + self.state[i] + self.x) & 0xFF
            self.state[i], self.state[self.x] = self.state[self.x], self.state[i]
        self.x = 0

    def crypt(self, input):
        output = [None] * len(input)
        for i in range(len(input)):
            self.x = (self.x + 1) & 0xFF
            self.y = (self.state[self.x] + self.y) & 0xFF
            self.state[self.x], self.state[self.y] = self.state[self.y], self.state[self.x]
            output[i] = self.chr((self.ord(input[i]) ^ self.state[(self.state[self.x] + self.state[self.y]) & 0xFF]))
        return b''.join(output)


class BasePage(object):
    ENCODING = 'iso-8859-15'

    def get_token(self):
        token = Attr('//form//input[@name="token"]', 'value', default=NotAvailable)(self.doc)
        if not token:
            try:
                token = Regexp(Attr('//body', 'onload'), "saveToken\('(.*?)'")(self.doc)
            except AttributeNotFound:
                self.logger.warning('Unable to update token.')
        return token

    def on_load(self):
        token = self.get_token()
        if token:
            self.browser.token = token
            self.logger.debug('Update token to %s', self.browser.token)

    def is_error(self):
        for script in self.doc.xpath('//script'):
            if script.text is not None and (
                "Le service est momentanément indisponible" in script.text
                or "Le service est temporairement indisponible" in script.text
                or "Votre abonnement ne vous permet pas d'accéder à ces services" in script.text
                or 'Merci de bien vouloir nous en excuser' in script.text
            ):
                return True

        return False

    def build_token(self, token):
        """
        Each time there is a call to SAB (selectActionButton), the token
        available in the form is modified with a key available in JS:

        ipsff(function(){TW().ipthk([12, 25, 17, 5, 23, 26, 15, 30, 6]);});

        Each value of the array is an index for the current token to append the
        char at this position at the end of the token.
        """
        table = None
        for script in self.doc.xpath('//script'):
            if script.text is None:
                continue
            m = re.search(r'ipthk\(([^\)]+)\)', script.text, flags=re.MULTILINE)
            if m:
                table = json.loads(m.group(1))
        if table is None:
            return token

        for i in table:
            token += token[i]
        return token

    def get_params(self):
        params = {}
        for field in self.doc.xpath('//input'):
            params[field.attrib['name']] = field.attrib.get('value', '')
        return params

    def get_button_actions(self):
        actions = {}
        for script in self.doc.xpath('//script'):
            if script.text is None:
                continue

            for id, action, strategy in re.findall(r'''attEvt\(window,"(?P<id>[^"]+)","click","sab\('(?P<action>[^']+)','(?P<strategy>[^']+)'\);"''', script.text, re.MULTILINE):
                actions[id] = {
                    'dialogActionPerformed': action,
                    'validationStrategy': strategy,
                }
        return actions

    def get_back_button_params(self, params=None, actions=None):
        btn = self.doc.xpath('.//button[span[text()="Retour"]]')
        if not btn:
            return

        params = params or self.get_params()
        actions = actions or self.get_button_actions()
        key = btn[0].attrib['id']
        assert actions.get(key), "Key %s not found in actions %s" % (key, actions)  # Currently it never happens
        params.update(actions[key])
        params['token'] = self.build_token(params['token'])
        return params


class MyHTMLPage(BasePage, HTMLPage):
    def build_doc(self, data, *args, **kwargs):
        # XXX FUCKING HACK BECAUSE BANQUE POPULAIRE ARE NASTY AND INCLUDE NULL
        # BYTES IN DOCUMENTS.
        data = data.replace(b'\x00', b'')
        return super(MyHTMLPage, self).build_doc(data, *args, **kwargs)


class RedirectPage(LoggedPage, MyHTMLPage):
    ENCODING = None

    """
    var i = 'lyhrnu551jo42yfzx0jm0sqk';
    setCookie('i', i);
    var welcomeMessage = decodeURI('M MACHIN');
    var lastConnectionDate = decodeURI('17 Mai 2013');
    var lastConnectionTime = decodeURI('14h27');
    var userId = '12345678';
    var userCat = '1';
    setCookie('uwm', $.rc4EncryptStr(welcomeMessage, i));
    setCookie('ulcd', $.rc4EncryptStr(lastConnectionDate, i));
    setCookie('ulct', $.rc4EncryptStr(lastConnectionTime, i));
    setCookie('uid', $.rc4EncryptStr(userId, i));
    setCookie('uc', $.rc4EncryptStr(userCat, i));
    var agentCivility = 'Mlle';
    var agentFirstName = decodeURI('Jeanne');
    var agentLastName = decodeURI('Machin');
    var agentMail = decodeURI('gary@example.org');
    setCookie('ac', $.rc4EncryptStr(agentCivility, i));
    setCookie('afn', $.rc4EncryptStr(agentFirstName, i));
    setCookie('aln', $.rc4EncryptStr(agentLastName, i));
    setCookie('am', $.rc4EncryptStr(agentMail, i));
    var agencyLabel = decodeURI('DTC');
    var agencyPhoneNumber = decodeURI('0123456789');
    setCookie('al', $.rc4EncryptStr(agencyLabel, i));
    setCookie('apn', $.rc4EncryptStr(agencyPhoneNumber, i));

    Note: that cookies are useless to login on website
    """

    def add_cookie(self, name, value):
        # httplib/cookielib don't seem to like unicode cookies...
        if sys.version_info.major < 3:
            name = to_unicode(name).encode('utf-8')
            value = to_unicode(value).encode('utf-8')
        self.browser.logger.debug('adding cookie %r=%r', name, value)
        self.browser.session.cookies.set(name, value, domain=urlsplit(self.url).hostname)

    def on_load(self):
        redirect_url = None
        args = {}
        RC4 = None
        for script in self.doc.xpath('//script'):
            if script.text is None:
                continue

            m = re.search('window.location=\'([^\']+)\'', script.text, flags=re.MULTILINE)
            if m:
                redirect_url = m.group(1)

            for line in script.text.split('\r\n'):
                m = re.match("^var (\w+) ?= ?[^']*'([^']*)'.*", line)
                if m:
                    args[m.group(1)] = m.group(2)

                m = re.match("^setCookie\('([^']+)', (\w+)\);", line)
                if m:
                    self.add_cookie(m.group(1), args[m.group(2)])

                m = re.match("^setCookie\('([^']+)', .*rc4EncryptStr\((\w+), \w+\)", line)
                if m:
                    enc = RC4.crypt(args[m.group(2)].encode('ascii'))
                    self.add_cookie(m.group(1), hexlify(enc).decode('ascii'))

                if RC4 is None and 'i' in args:
                    RC4 = WikipediaARC4(args['i'].encode('ascii'))

        if redirect_url is not None:
            url = self.browser.absurl(redirect_url)
            headers = {'Referer': self.url}
            self.browser.logger.debug('redir...')
            self.browser.location(url, headers=headers)

        try:
            form = self.get_form(name="CyberIngtegrationPostForm")
        except FormNotFound:
            pass
        else:
            form.submit()


class ErrorPage(LoggedPage, MyHTMLPage):
    def on_load(self):
        if CleanText('//script[contains(text(), "momentanément indisponible")]')(self.doc):
            raise BrowserUnavailable("Le service est momentanément indisponible")
        elif CleanText('//h1[contains(text(), "Cette page est indisponible")]')(self.doc):
            raise BrowserUnavailable('Cette page est indisponible')
        return super(ErrorPage, self).on_load()

    def get_token(self):
        try:
            buf = self.doc.xpath('//body/@onload')[0]
        except IndexError:
            return
        else:
            m = re.search("saveToken\('([^']+)'\)", buf)
            if m:
                return m.group(1)


class UnavailablePage(LoggedPage, MyHTMLPage):
    def on_load(self):
        h1 = CleanText('//h1[1]')(self.doc)
        if "est indisponible" in h1:
            raise BrowserUnavailable(h1)
        body = CleanText(".")(self.doc)
        if "An unexpected error has occurred." in body or "Une erreur s'est produite" in body:
            raise BrowserUnavailable(body)

        a = Link('//a[@class="btn"][1]', default=None)(self.doc)
        if not a:
            raise BrowserUnavailable()
        self.browser.location(a)


class NewLoginPage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'new_login'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'


class JsFilePage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'js_file'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'

    def get_user_info_client_id(self):
        return Regexp(pattern=r'anonymous:{clientId:"([^"]+)"').filter(self.text)


class AuthorizePage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'authorize'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'


class LoginTokensPage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'login_tokens'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'

    def get_expires_in(self):
        return Dict('parameters/expires_in')(self.doc)


class InfoTokensPage(JsonPage):
    def get_access_token(self):
        return Dict('access_token')(self.doc)

    def get_user_type(self):
        user_subscription = Dict('characteristics/subscribeTypeItems/0/label')(self.doc)
        user_types = {
            'Particulier': 'part',
            'Personne Protégé': 'part',
            'Personne Morale': 'ent',
            'EI': 'pro',
        }
        return MapIn(self.doc, user_types).filter(user_subscription)


class VkImagePage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'vk_image'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'


class AuthenticationMethodPage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'authentication_method_page'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'

    def get_redirect_data(self):
        return Dict('response/saml2_post', default=NotAvailable)(self.doc)

    def is_new_login(self):
        # We check here if we are doing a new login
        return bool(Dict('step/phase/state', default=NotAvailable)(self.doc))

    def get_status(self):
        return Dict('response/status', default=NotAvailable)(self.doc)


class AuthenticationStepPage(AbstractPage):
    PARENT = 'caissedepargne'
    PARENT_URL = 'authentication_step'
    BROWSER_ATTR = 'package.browser.CaisseEpargne'

    def get_status(self):
        return Coalesce(
            Dict('response/status', default=NotAvailable),
            Dict('phase/state', default=NotAvailable)
        )(self.doc)


class LoginPage(MyHTMLPage):
    def on_load(self):
        h1 = CleanText('//h1[1]')(self.doc)

        if h1.startswith('Le service est moment'):
            text = CleanText('//h4[1]')(self.doc) or h1
            raise BrowserUnavailable(text)

        if not self.browser.no_login:
            raise LoggedOut()

    def login(self, login, passwd):
        form = self.get_form(name='Login')
        form['IDToken1'] = login.encode(self.ENCODING)
        form['IDToken2'] = passwd.encode(self.ENCODING)
        form.submit()


class CaissedepargneVirtKeyboard(SplitKeyboard):
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
        super(CaissedepargneVirtKeyboard, self).__init__(code_to_filedata)


class MyVirtKeyboard(SplitKeyboard):
    char_to_hash = {
        '0': 'cce0f72c47c74a3dde57c4fdbcda1db4',
        '1': 'f5d22afb3ece4dec73bd8a2a4c2844da',
        '2': '6d3e5db2ccac3f2c13c1f0ba22571857',
        '3': ('c8e4f6addac4d322f0f9668d472a146c', '34d0566ea3f2330c675365da3178f6ab'),
        '4': '8a8c769418ec829c208ed442fbf5fe77',
        '5': '2c3ae480bc91f73b431b048b584026c7',
        '6': 'a80d639443818e838b434c36dd518df5',
        '7': '8e59048702e4c5f89bbbc1a598d06d1e',
        '8': '46bc59a5b288c63477ff52811a3961c5',
        '9': 'a7bf34568154ef91e990aa5bade3e946',
    }

    codesep = ' '

    def convert(self, buffer):
        im = Image.open(BytesIO(buffer))
        im = im.resize((5, 8), Image.BICUBIC)
        im = im.filter(ImageFilter.UnsharpMask(radius=2,
                                               percent=110,
                                               threshold=3))
        im = im.convert("L", dither=Image.NONE)
        im = Image.eval(im, lambda x: 0 if x < 160 else 255)
        s = BytesIO()
        im.save(s, 'png')
        return s.getvalue()


class Login2Page(LoginPage):
    @property
    def request_url(self):
        transactionID = self.params['transactionID']
        assert transactionID
        return self.browser.redirect_url + transactionID

    def on_load(self):
        if not self.browser.no_login:
            raise LoggedOut()

    def set_form_ids(self):
        r = self.browser.open(self.request_url)
        doc = r.json()

        self.form_id, = [(k, v[0]['id'], v[0]['type']) for k, v in doc['step']['validationUnits'][0].items() if v[0]['type'] in ('PASSWORD_LOOKUP', 'IDENTIFIER')]

    def virtualkeyboard(self, vk_obj, password):
        imgs = {}
        lst_img = self.browser.location(vk_obj['externalRestMediaApiUrl']).json()
        for img_info in lst_img:
            value = img_info['value']
            url = img_info['uri']

            resp = self.browser.location(url)
            imgs[value] = resp.content

        return MyVirtKeyboard(imgs).get_string_code(password)

    def login(self, login, password):
        payload = {
            'validate': {
                self.form_id[0]: [{
                    'id': self.form_id[1],
                    'login': login.upper(),
                    'password': password,
                    'type': 'PASSWORD_LOOKUP',
                }]
            }
        }
        url = self.request_url + '/step'
        if self.form_id[2] == 'IDENTIFIER':
            del payload['validate'][self.form_id[0]][0]['password']
            payload['validate'][self.form_id[0]][0]['type'] = 'IDENTIFIER'
            doc = self.browser.open(url, json=payload).json()

            for k, v in doc['validationUnits'][0].items():
                if v[0]['type'] in ('PASSWORD',):
                    form_id = (k, v[0]['id'], v[0]['type'])

                if v[0].get('virtualKeyboard'):
                    if not password.isdigit():
                        # Users who get the virtual keyboard needs a password with digits only
                        raise BrowserIncorrectPassword()
                    password = self.virtualkeyboard(vk_obj=v[0]['virtualKeyboard'],
                                                    password=password)

            payload = {
                'validate': {
                    form_id[0]: [{
                        'id': form_id[1],
                        'password': password,
                        'type': 'PASSWORD',
                    }]
                }
            }
        r = self.browser.open(url, json=payload)

        doc = r.json()
        self.logger.debug('doc = %s', doc)
        if 'phase' in doc and doc['phase']['state'] == 'TERMS_OF_USE':
            # Got:
            # {u'phase': {u'state': u'TERMS_OF_USE'}, u'validationUnits': [{u'LIST_OF_TERMS': [{u'type': u'TERMS', u'id': u'b7f28f91-7aa0-48aa-8028-deec13ae341b', u'reference': u'CGU_CYBERPLUS'}]}]}
            if 'reference' in doc['validationUnits'][0]:
                del doc['validationUnits'][0]['reference']
            elif 'reference' in doc['validationUnits'][0]['LIST_OF_TERMS'][0]:
                del doc['validationUnits'][0]['LIST_OF_TERMS'][0]['reference']
            payload = {'validate': doc['validationUnits'][0]}

            url = self.request_url + '/step'
            r = self.browser.open(url, json=payload)
            doc = r.json()
            self.logger.debug('doc = %s', doc)

        if 'phase' in doc and doc['phase']['state'] == "ENROLLMENT":
            raise ActionNeeded()

        if (('phase' in doc and doc['phase']['previousResult'] == 'FAILED_AUTHENTICATION')
           or doc['response']['status'] != 'AUTHENTICATION_SUCCESS'):
            raise BrowserIncorrectPassword()

        data = {'SAMLResponse': doc['response']['saml2_post']['samlResponse']}
        self.browser.location(doc['response']['saml2_post']['action'], data=data)


class AlreadyLoginPage(LoggedPage, MyHTMLPage):
    def is_here(self):
        try:
            doc = json.loads(self.response.text)
            if 'response' in doc:
                return doc['response']['status'] == 'AUTHENTICATION_SUCCESS' and 'saml2_post' in doc['response']
        except ValueError:
            # not a json page
            # so it should be Login2Page
            return False
        return False


class IndexPage(LoggedPage, MyHTMLPage):
    def get_token(self):
        url = self.doc.xpath('//frame[@name="portalHeader"]')[0].attrib['src']
        v = urlsplit(url)
        args = dict(parse_qsl(v.query))
        return args['token']


class HomePage(LoggedPage, MyHTMLPage):
    # Sometimes, the page is empty but nothing is scrapped on it.
    def build_doc(self, data, *args, **kwargs):
        if not data:
            return None
        return super(MyHTMLPage, self).build_doc(data, *args, **kwargs)

    @retry(KeyError)
    # sometime the server redirects to a bad url, not containing token.
    # therefore "return args['token']" crashes with a KeyError
    def get_token(self):
        vary = None
        if self.params.get('vary', None) is not None:
            vary = self.params['vary']
        else:
            for script in self.doc.xpath('//script'):
                if script.text is None:
                    continue

                m = re.search("'vary', '([\d-]+)'\)", script.text)
                if m:
                    vary = m.group(1)
                    break

        url = self.browser.absurl('/portailinternet/Transactionnel/Pages/CyberIntegrationPage.aspx')
        headers = {'Referer': self.url}

        # Sometime, the page is a 302 and redirect to a page where there are no information that we need,
        # so we try with 3 others url to further fetch token when empty page
        r = self.browser.open(url, data='taskId=aUniversMesComptes', params={'vary': vary}, headers=headers)

        if not int(r.headers.get('Content-Length', 0)):
            r = self.browser.open(url, data='taskId=aUniversMesComptes', headers=headers)

        if not int(r.headers.get('Content-Length', 0)):
            r = self.browser.open(url, data={'taskId': 'equipementDom'}, params={'vary': vary}, headers=headers)

        if not int(r.headers.get('Content-Length', 0)):
            r = self.browser.open(url)

        doc = r.page.doc
        date = None
        for script in doc.xpath('//script'):
            if script.text is None:
                continue

            m = re.search('lastConnectionDate":"([^"]*)"', script.text)
            if m:
                date = m.group(1)

        url = self.browser.absurl('/cyber/ibp/ate/portal/integratedInternet.jsp')
        data = 'session%%3Aate.lastConnectionDate=%s&taskId=aUniversMesComptes' % date
        headers = {'Referer': r.url}
        r = self.browser.open(url, data=data, headers=headers)

        v = urlsplit(r.url)
        args = dict(parse_qsl(v.query))
        return args['token']


class GenericAccountsPage(LoggedPage, MyHTMLPage):
    ACCOUNT_TYPES = {
        'Mes comptes d\'épargne': Account.TYPE_SAVINGS,
        'Mon épargne': Account.TYPE_SAVINGS,
        'Placements': Account.TYPE_SAVINGS,
        'Liste complète de mon épargne': Account.TYPE_SAVINGS,
        'Mes comptes': Account.TYPE_CHECKING,
        'Comptes en euros': Account.TYPE_CHECKING,
        'Mes comptes en devises': Account.TYPE_CHECKING,
        'Liste complète de mes comptes': Account.TYPE_CHECKING,
        'Mes emprunts': Account.TYPE_LOAN,
        'Liste complète de mes emprunts': Account.TYPE_LOAN,
        'Financements': Account.TYPE_LOAN,
        'Liste complète de mes engagements': Account.TYPE_LOAN,
        'Mes services': None,  # ignore this kind of accounts (no bank ones)
        'Équipements': None,  # ignore this kind of accounts (no bank ones)
        'Synthèse': None,  # ignore this title
    }

    ACCOUNT_PATTERNS = [
        (re.compile(r'.*Titres Pea.*'), Account.TYPE_PEA),
        (re.compile(r".*Plan D'epargne En Actions.*"), Account.TYPE_PEA),
        (re.compile(r".*Compte Especes Pea.*"), Account.TYPE_PEA),
        (re.compile(r'.*Plan Epargne Retraite.*'), Account.TYPE_PERP),
        (re.compile(r'.*Titres.*'), Account.TYPE_MARKET),
        (re.compile(r'.*Selection Vie.*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^Fructi Pulse.*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^Fructi Neo.*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^(Quintessa|Solevia|Irriga|Delfea).*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^Plan Epargne Enfant Mul.*'), Account.TYPE_MARKET),
        (re.compile(r'^Alc Premium'), Account.TYPE_MARKET),
        (re.compile(r'^Plan Epargne Enfant Msu.*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^Parts? Sociales?.*'), Account.TYPE_MARKET),
        (re.compile(r'^Contrat Generali.*'), Account.TYPE_LIFE_INSURANCE),
        (re.compile(r'^Reserve Facelia.*'), Account.TYPE_REVOLVING_CREDIT),
    ]

    def pop_up(self):
        if self.doc.xpath('//span[contains(text(), "du navigateur Internet.")]'):
            return True
        return False

    def is_short_list(self):
        return len(self.doc.xpath('//script[contains(text(), "EQUIPEMENT_COMPLET")]')) > 0

    COL_NUMBER = 0
    COL_TYPE = 1
    COL_LABEL = 2
    COL_BALANCE = 3
    COL_COMING = 4

    def get_next_params(self):
        # Same mechanism than get_next_params in TransactionsPage
        nxt = self.doc.xpath('//li[contains(@id, "_nxt")]')
        if len(nxt) == 0 or nxt[0].attrib.get('class', '') == 'nxt-dis':
            return None

        params = {}
        for field in self.doc.xpath('//input'):
            params[field.attrib['name']] = field.attrib.get('value', '')

        params['validationStrategy'] = 'NV'
        params['pagingDirection'] = 'NEXT'
        params['pagerName'] = nxt[0].attrib['id'].split('_', 1)[0]

        return params

    def iter_accounts(self, next_pages, accounts_parsed=None, next_with_params=None):
        account_type = Account.TYPE_UNKNOWN

        params = self.get_params()
        actions = self.get_button_actions()

        for div in self.doc.xpath('//div[has-class("btit")]'):
            if div.text in (None, 'Synthèse'):
                continue
            account_type = self.ACCOUNT_TYPES.get(div.text.strip(), Account.TYPE_UNKNOWN)

            if account_type is None:
                # ignore services accounts
                self.logger.debug('Ignore account type %s', div.text.strip())
                continue

            # Go to the full list of this kind of account, if any.
            btn = div.getparent().xpath('.//button[span[text()="Suite"]]')
            if len(btn) > 0:
                _params = params.copy()
                _params.update(actions[btn[0].attrib['id']])
                next_pages.append(_params)
                continue

            currency = None
            for th in div.getnext().xpath('.//thead//th'):
                m = re.match('.*\((\w+)\)$', th.text)
                if m and currency is None:
                    currency = Account.get_currency(m.group(1))

            for tr in div.getnext().xpath('.//tbody/tr'):
                if 'id' not in tr.attrib:
                    continue

                args = dict(parse_qsl(tr.attrib['id']))
                tds = tr.findall('td')

                if len(tds) < 4 or 'identifiant' not in args:
                    self.logger.warning('Unable to parse an account')
                    continue

                account = Account()
                account.id = args['identifiant'].replace(' ', '')
                account.number = account.id
                account.label = ' '.join([''.join([txt.strip() for txt in tds[1].itertext()]),
                                           ''.join([txt.strip() for txt in tds[2].itertext()])]).strip()

                for pattern, _type in self.ACCOUNT_PATTERNS:
                    match = pattern.match(account.label)
                    if match:
                        account.type = _type
                        break
                    else:
                        account.type = account_type

                balance_text = ''.join([txt.strip() for txt in tds[3].itertext()])
                balance = FrenchTransaction.clean_amount(balance_text)
                account.balance = Decimal(balance or '0.0')
                account.currency = currency or Account.get_currency(balance_text)

                if account.type == account.TYPE_LOAN:
                    account.balance = - abs(account.balance)

                account._prev_debit = None
                account._next_debit = None
                account._params = None
                account._coming_params = None
                account._coming_count = None
                account._invest_params = None
                account._loan_params = None

                if balance != '' and len(tds[3].xpath('.//a')) > 0:
                    account._params = params.copy()
                    account._params['dialogActionPerformed'] = 'SOLDE'
                    account._params['attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]] = tr.attrib['id'].split('_', 1)[1]

                if len(tds) >= 5 and len(tds[self.COL_COMING].xpath('.//a')) > 0:
                    _params = account._params.copy()
                    _params['dialogActionPerformed'] = 'ENCOURS_COMPTE'
                    _params['attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]] = tr.attrib['id'].split('_', 1)[1]

                    # If there is an action needed before going to the cards page, save it.
                    m = re.search('dialogActionPerformed=([\w_]+)', self.url)
                    if m and m.group(1) != 'EQUIPEMENT_COMPLET':
                        _params['prevAction'] = m.group(1)
                    next_pages.append(_params)

                if not account._params:
                    account._invest_params = params.copy()
                    account._invest_params['dialogActionPerformed'] = 'CONTRAT'
                    account._invest_params['attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]] = tr.attrib['id'].split('_', 1)[1]

                yield account

        if not next_with_params:
            # Needed to preserve navigation.
            # But if we have a next_with_params we don't want to come back
            # to main accounts page (the next page would be not reachable)
            self.browser.follow_back_button_if_any(params=params.copy(), actions=actions)


class AccountsPage(GenericAccountsPage):
    pass


class AccountsNextPage(GenericAccountsPage):
    def is_here(self):
        # The url can be exactly the same than in TransactionsPage
        # we can differentiate them here
        return bool(self.doc.xpath(
            '''//span[@class="pmsg" and contains(text(), "Pour accéder au détail d'un contrat")]'''
        ))


class AccountsFullPage(GenericAccountsPage):
    pass


class CardsPage(LoggedPage, MyHTMLPage):
    COL_ID = 1
    COL_TYPE = 2
    COL_LABEL = 3
    COL_DATE = 4
    COL_AMOUNT = 5

    def iter_accounts(self, next_pages, accounts_parsed=None, next_with_params=None):
        params = self.get_params()

        account = None
        currency = None
        for th in self.doc.xpath('//table[@id="tbl1"]//thead//th'):
            m = re.match('.*\((\w+)\)$', th.text)
            if m and currency is None:
                currency = Account.get_currency(m.group(1))

        if currency is None:
            currency = Account.get_currency(CleanText('//td[@id="tbl1_0_5_Cell"]//span')(self.doc))

        for tr in self.doc.xpath('//table[@id="tbl1"]/tbody/tr'):
            cols = tr.xpath('./td')

            if len(cols) == 1 and CleanText('.')(cols[0]) == 'pas de carte':
                self.logger.debug('there are no cards on this page')
                continue

            # We are processing another card, so reset account
            if CleanText('.')(cols[0]) and account is not None:
                yield account
                account = None

            id = CleanText(None).filter(cols[self.COL_ID])
            if len(id) > 0:
                if account is not None:
                    yield account
                account = Account()
                account.id = id.replace(' ', '')
                account.number = account.id
                account.type = Account.TYPE_CARD
                account.balance = account.coming = Decimal('0')
                account._next_debit = datetime.date.today()
                account._prev_debit = datetime.date(2000, 1, 1)
                account.label = ' '.join([CleanText(None).filter(cols[self.COL_TYPE]),
                                           CleanText(None).filter(cols[self.COL_LABEL])])
                account.currency = currency

                if accounts_parsed is not None:
                    for account_parsed in accounts_parsed:
                        if (account_parsed.type == Account.TYPE_CHECKING
                           and account_parsed.id.replace('CPT', '') == Regexp(CleanText('//div[@class="btit"]'), r'(\d+)$')(self.doc)):
                                account.parent = account_parsed

                account._params = None
                account._invest_params = None
                account._coming_params = params.copy()
                account._coming_params['dialogActionPerformed'] = 'SELECTION_ENCOURS_CARTE'
                account._coming_params['attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]] = tr.attrib['id'].split('_', 1)[1]

                # select current row and next rows till parent name is empty
                account._coming_start = int(tr.attrib['id'].split('_', 1)[1])
                account._coming_count = 1
                for row in tr.xpath('./following-sibling::tr[./td[5]/span[not(contains(text(), "(1)"))]]'):
                    if CleanText('./td[2]')(row):
                        break
                    account._coming_count += 1

            elif account is None:
                raise BrokenPageError('Unable to find accounts on cards page')
            else:
                account._params = params.copy()
                account._params['dialogActionPerformed'] = 'SELECTION_ENCOURS_CARTE'
                account._params['attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]] = tr.attrib['id'].split('_', 1)[1]

            date_col = CleanText(None).filter(cols[self.COL_DATE])
            m = re.search('(\d+)/(\d+)/(\d+)', date_col)
            if not m:
                self.logger.warning('Unable to parse date %r' % date_col)
                continue

            date = datetime.date(*[int(c) for c in m.groups()][::-1])
            if date.year < 100:
                date = date.replace(year=date.year + 2000)

            amount = Decimal(FrenchTransaction.clean_amount(CleanText(None).filter(cols[self.COL_AMOUNT])))

            if not date_col.endswith('(1)'):
                # debited
                account.coming += - abs(amount)
                account._next_debit = date
            elif date > account._prev_debit:
                account._prev_balance = - abs(amount)
                account._prev_debit = date

        if account is not None:
            yield account

        if not next_with_params:
            # Needed to preserve navigation
            # But if we have a next_with_params we don't want to come back
            # to main accounts page (the next page would be not reachable)
            self.browser.follow_back_button_if_any(params=params.copy())


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile('^RET DAB (?P<text>.*?) RETRAIT (DU|LE) (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d+).*'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile('^RET DAB (?P<text>.*?) CARTE ?:.*'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile('^(?P<text>.*) RETRAIT DU (?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) .*'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile('^(RETRAIT CARTE )?RET(RAIT)? DAB (?P<text>.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile('((\w+) )?(?P<dd>\d{2})(?P<mm>\d{2})(?P<yy>\d{2}) CB[:\*][^ ]+ (?P<text>.*)'), FrenchTransaction.TYPE_CARD),
        (re.compile('^VIR(EMENT)? (?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile('^(PRLV|PRELEVEMENT) (?P<text>.*)'), FrenchTransaction.TYPE_ORDER),
        (re.compile('^(?P<text>CHEQUE .*)'), FrenchTransaction.TYPE_CHECK),
        (re.compile('^(AGIOS /|FRAIS) (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile('^(CONVENTION \d+ )?COTIS(ATION)? (?P<text>.*)', re.IGNORECASE), FrenchTransaction.TYPE_BANK),
        (re.compile('^REMISE (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile('^(?P<text>ECHEANCE PRET .*)'), FrenchTransaction.TYPE_LOAN_PAYMENT),
        (re.compile('^(?P<text>.*)( \d+)? QUITTANCE .*'), FrenchTransaction.TYPE_ORDER),
        (re.compile('^.* LE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})$'), FrenchTransaction.TYPE_UNKNOWN),
        (re.compile(r'^RELEVE CARTE'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^RET GAB .*'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^RETRAIT CARTE AGENCE \d+$'), FrenchTransaction.TYPE_WITHDRAWAL),
    ]


class InvestmentPage(LoggedPage, HTMLPage):
    def get_investment_page_params(self):
        script = self.doc.xpath('//body')[0].attrib['onload']
        url = None
        m = re.search(r"','([^']+?)',\[", script, re.MULTILINE)
        if m:
            url = m.group(1)

        params = {}
        for key, value in re.findall(r"key:'(?P<key>SJRToken)'\,value:'(?P<value>.*?)'}", script, re.MULTILINE):
            params[key] = value

        if url and params:
            return url, params
        return None


class TransactionsPage(LoggedPage, MyHTMLPage):
    @method
    class fill_loan(ItemElement):
        obj_name = CleanText('//span[@id="IntituleContrat"]')
        obj_subscription_date = Date(CleanText('//span[@id="DateSouscription"]'), dayfirst=True, default=NotAvailable)
        obj_maturity_date = Date(CleanText('//span[@id="DateEcheance"]'), dayfirst=True, default=NotAvailable)

        def obj_duration(self):
            duration = CleanDecimal.French('//span[@id="Duree"]', default=None)(self)
            if duration is not None:
                return int(duration)
            return NotAvailable

        obj_total_amount = CleanDecimal.French('//span[@id="Nominal"]', default=NotAvailable)
        obj_next_payment_date = Date(CleanText('//span[@id="ProchaineEcheance"]'), dayfirst=True, default=NotAvailable)
        obj_next_payment_amount = CleanDecimal.French('//span[@id="MontantEcheance"]', default=NotAvailable)
        obj_rate = CleanDecimal.French('//span[@id="TEG"]', default=NotAvailable)

    def is_sorted_by_most_recent(self):
        # If the transactions are not sorted correctly, the class of this
        # 'a' tag changes ('tcth' if sorted the other way, 'tctm' if not sorted
        # by operation date)
        return CleanText('//a[@class="tctb" and contains(text(), "Date opé")]')(self.doc)

    def get_next_params(self):
        nxt = self.doc.xpath('//li[contains(@id, "_nxt")]')
        if len(nxt) == 0 or nxt[0].attrib.get('class', '') == 'nxt-dis':
            return None

        params = {}
        for field in self.doc.xpath('//input'):
            params[field.attrib['name']] = field.attrib.get('value', '')

        params['validationStrategy'] = 'NV'
        params['pagingDirection'] = 'NEXT'
        params['pagerName'] = nxt[0].attrib['id'].split('_', 1)[0]

        return params

    def get_history(self, account, coming):
        if len(self.doc.xpath('//table[@id="tbl1"]')) > 0:
            return self.get_account_history()
        if len(self.doc.xpath('//table[@id="TabFact"]')) > 0:
            return self.get_card_history(account, coming)

        raise NotImplementedError('Unable to find what kind of history it is.')

    COL_COMPTA_DATE = 0
    COL_LABEL = 1
    COL_REF = 2  # optional
    COL_OP_DATE = -4
    COL_VALUE_DATE = -3
    COL_DEBIT = -2
    COL_CREDIT = -1

    def get_account_history(self):
        # Keep track of the order in the transaction list, so details can be retrieve
        # Because each transaction row has a different id
        # in the html page for each request of the TransactionPage
        for tr in self.doc.xpath('//table[@id="tbl1"]/tbody/tr'):
            tds = tr.findall('td')

            if len(tds) < 5:
                continue

            t = Transaction()

            # get the column index of the link to access transaction details
            # (only used for GoCardLess transactions so far)
            t._has_link = bool(tds[self.COL_DEBIT].findall('a') or tds[self.COL_CREDIT].findall('a'))

            # Default sort on website is by compta date, in browser.py we
            # change the sort on operation date.
            cleaner = CleanText(None).filter
            date = cleaner(tds[self.COL_OP_DATE])
            vdate = cleaner(tds[self.COL_VALUE_DATE])
            raw = cleaner(tds[self.COL_LABEL])
            debit = cleaner(tds[self.COL_DEBIT])
            credit = cleaner(tds[self.COL_CREDIT])

            t.bdate = Date(dayfirst=True).filter(cleaner(tds[self.COL_COMPTA_DATE]))
            t.parse(date, re.sub(r'[ ]+', ' ', raw), vdate)
            t.set_amount(credit, debit)
            t._amount_type = 'debit' if t.amount == debit else 'credit'

            # Strip the balance displayed in transaction labels
            t.label = re.sub('solde en valeur : .*', '', t.label)
            t.raw = re.sub('solde en valeur : .*', '', t.raw)

            # XXX Fucking hack to include the check number not displayed in the full label.
            if re.match("^CHEQUE |^CHQ VOTRE CHEQUE", t.label):
                t.raw = '%s No: %s' % (t.raw, cleaner(tds[self.COL_REF]))

            # In rare cases, label is empty ..
            if not t.label:
                t.label = cleaner(tds[self.COL_REF])

            # To be able to find by ref on the transaction page
            t._ref = cleaner(tds[self.COL_REF])

            yield t

    COL_CARD_DATE = 0
    COL_CARD_LABEL = 1
    COL_CARD_AMOUNT = 2

    def get_card_history(self, account, coming):
        if coming:
            debit_date = account._next_debit
        elif not hasattr(account, '_prev_balance'):
            return
        else:
            debit_date = account._prev_debit
            if 'ContinueTask.do' in self.url:
                t = Transaction()
                t.parse(debit_date, 'RELEVE CARTE')
                t.amount = -account._prev_balance
                yield t

        currency = Account.get_currency(self.doc
                                        .xpath('//table[@id="TabFact"]/thead//th')[self.COL_CARD_AMOUNT]
                                        .text
                                        .replace('(', ' ')
                                        .replace(')', ' '))
        for i, tr in enumerate(self.doc.xpath('//table[@id="TabFact"]/tbody/tr')):
            tds = tr.findall('td')

            if len(tds) < 3:
                continue

            t = Transaction()

            cleaner = CleanText(None).filter
            date = cleaner(tds[self.COL_CARD_DATE])
            label = cleaner(tds[self.COL_CARD_LABEL])
            amount = '-' + cleaner(tds[self.COL_CARD_AMOUNT])

            t.parse(debit_date, re.sub(r'[ ]+', ' ', label))
            t.set_amount(amount)
            t.rdate = t.bdate = t.parse_date(date)
            t.original_currency = currency
            if not t.type:
                t.type = Transaction.TYPE_DEFERRED_CARD

            yield t

    def no_operations(self):
        if len(self.doc.xpath('//table[@id="tbl1" or @id="TabFact"]//td[@colspan]')) > 0:
            return True
        if len(self.doc.xpath('//div[contains(text(), "Accès à LineBourse")]')) > 0:
            return True

        return False

    def get_transaction_table_id(self, ref):
        tr = self.doc.xpath('//table[@id="tbl1"]/tbody/tr[.//span[contains(text(), "%s")]]' % ref)[0]

        key = 'attribute($SEL_$%s)' % tr.attrib['id'].split('_')[0]
        value = tr.attrib['id'].split('_', 1)[1]

        return key, value

    def get_gocardless_strategy_param(self, transaction):
        # A form is filled and send with javascript
        # the 'validationStrategy' parameter value only depends on the column
        # index in which the link lies
        #
        # To get more details about how things are done, see the following javascript functions:
        # - attachTableRowEvents (atre)
        # - attachActiveSelectionEventsOnRow
        # - astr
        # - updateSelection (uds)
        # - selectActionButton (sab)
        # - a script element embedded in the html page (search for "tcl5", "tcl6")

        assert transaction._has_link

        if transaction._amount_type == 'debit':
            return 'AV'
        elif transaction._amount_type == 'credit':
            return 'NV'

    def go_investment(self):
        script = self.doc.xpath('//body')[0].attrib['onload']
        if re.search(r'startWebAppTask\(', script) is None:
            return False
        params = {'oid': re.search(r"'urlReturn',\w+?,'(\w+)'\)", script).group(1)}
        self.browser.location(self.browser.absurl('/cyber/ibp/ate/skin/internet/pages/webAppReroutingAutoSubmit.jsp'), params=params)
        return True


class NatixisChoicePage(LoggedPage, HTMLPage):
    def on_load(self):
        message = CleanText('//span[@class="rf-msgs-sum"]', default='')(self.doc)
        if re.search(r"Le service de consultation de votre contrat \w+ est momentanément indisponible.", message):
            raise BrowserUnavailable()
        # TODO handle when there are multiple accounts on this page
        account_tr, = self.doc.xpath('//tbody[@id="list:dataVie:tb"]/tr')
        self.logger.info('opening automatically account %s', CleanText('./td[1]')(account_tr))
        self.browser.location(Link('./td[1]/a')(account_tr))


class NatixisPage(LoggedPage, HTMLPage):
    def on_load(self):
        form = self.get_form(name="formRoutage")
        form['javax.faces.source'] = 'formRoutageButton'
        form['javax.faces.partial.execute'] = 'formRoutageButton @component'
        form['javax.faces.partial.render'] = '@component'
        form['AJAX:EVENTS_COUNT'] = '1'
        form['javax.faces.partial.ajax'] = 'true'
        form['javax.faces.partial.event'] = 'click'
        form['org.richfaces.ajax.component'] = 'formRoutageButton'
        form['formRoutageButton'] = 'formRoutageButton'
        form.request.headers['Faces-Request'] = 'partial/ajax'
        form.submit()


class TransactionsBackPage(TransactionsPage):
    def is_here(self):
        return self.doc.xpath('//div[text()="Liste des écritures"]')


class NatixisRedirect(LoggedPage, XMLPage):
    def get_redirect(self):
        url = self.doc.xpath('/partial-response/redirect/@url')[0]
        return url.replace('http://', 'https://')  # why do they use http on a bank site???


class NatixisErrorPage(LoggedPage, HTMLPage):
    pass


class IbanPage(LoggedPage, MyHTMLPage):
    def need_to_go(self):
        return len(self.doc.xpath('//div[@class="grid"]/div/span[contains(text(), "IBAN")]')) == 0

    def go_iban(self, account):
        for tr in self.doc.xpath('//table[@id]/tbody/tr'):
            conditions = (
                account.type not in (Account.TYPE_LOAN, Account.TYPE_MARKET),
                CleanText().filter(tr.xpath('./td[1]')) in account.id,
                self.doc.xpath('//div[contains(text(), "Impression IBAN/RIB")]'),
            )
            if all(conditions):
                form = self.get_form(id='myForm')
                form['token'] = self.build_token(form['token'])
                form['dialogActionPerformed'] = "DETAIL_IBAN_RIB"
                tr_id = Attr(None, 'id').filter(tr.xpath('.')).split('_')
                form['attribute($SEL_$%s)' % tr_id[0]] = tr_id[1]
                form.submit()
                return True
        return False

    def get_iban(self, acc_id):
        iban_class = None
        for div in self.doc.xpath('//div[@class="grid"]/div'):
            if not iban_class and "IBAN" in CleanText().filter(div.xpath('./span')):
                iban_class = Attr(None, 'class').filter(div.xpath('.'))
            elif iban_class is not None and iban_class == Attr(None, 'class').filter(div.xpath('.')):
                iban = CleanText().filter(div.xpath('.')).replace(' ', '')
                if re.sub('\D', '', acc_id) in iban:
                    return iban
        return NotAvailable


class EtnaPage(LoggedPage, MyHTMLPage):
    pass


def float_to_decimal(f):
    # Decimal(float_value) gives horrible results, convert to str first
    return Decimal(str(f))


class NatixisInvestPage(LoggedPage, JsonPage):
    @method
    class iter_investments(DictElement):
        item_xpath = 'detailContratVie/valorisation/supports'

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(Dict('nom'))

            def obj_code(self):
                # Sometimes the 'codeIsin' key is not even in the JSON
                code = Dict('codeIsin', default=None)(self)
                if code:
                    return IsinCode(CleanText(Dict('codeIsin')), default=NotAvailable)(self)
                return NotAvailable

            def obj_code_type(self):
                code = Dict('codeIsin', default=None)(self)
                if code:
                    return IsinType(CleanText(Dict('codeIsin')), default=NotAvailable)(self)
                return NotAvailable

            def obj_vdate(self):
                dt = Dict('dateValeurUniteCompte', default=None)(self)
                if dt is None:
                    dt = self.page.doc['detailContratVie']['valorisation']['date']
                return Date().filter(dt)

            obj_valuation = Eval(float_to_decimal, Dict('montant'))
            obj_quantity = Eval(float_to_decimal, Dict('nombreUnitesCompte'))
            obj_unitvalue = Eval(float_to_decimal, Dict('valeurUniteCompte'))
            obj_portfolio_share = Eval(lambda x: float_to_decimal(x) / 100, Dict('repartition'))


class NatixisHistoryPage(LoggedPage, JsonPage):
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
                return bool(obj.raw) and bool(obj.date)


def use_invest_date(tr):
    dates = [invest.vdate for invest in tr.investments]
    if not dates:
        return

    assert all(d == dates[0] for d in dates)
    tr.date = dates[0]


class NatixisDetailsPage(LoggedPage, RawPage):
    def build_doc(self, data):
        return list(get_pdf_rows(data))

    COL_LABEL = 0
    COL_DATE = 1
    COL_TR_AMOUNT = 2
    COL_VALUATION = 3
    COL_UNITVALUE = 4
    COL_QUANTITY = 5

    # warning: tr amount is "brut", unlike invest amounts ("net")...

    def get_history(self):
        tr = None

        for page in self.doc:
            for n, row in enumerate(page):
                if len(row) != 7:
                    continue

                label = ' '.join(row[self.COL_LABEL])

                if row[self.COL_TR_AMOUNT]:
                    if tr is not None:
                        if n == 0 and label == tr.label:
                            self.logger.debug('%r seems to continue on next page', tr)
                            continue
                        yield tr

                        tr = None

                    if not label:
                        # this pdf is really cryptic...
                        # we assume blue rows are a new transaction
                        # but if no label, it doesn't appear in the website json
                        continue

                    tr = Transaction()
                    tr.type = Transaction.TYPE_BANK
                    tr.raw = tr.label = label
                    tr.amount = CleanDecimal(replace_dots=True).filter(''.join(row[self.COL_TR_AMOUNT]))
                elif not row[self.COL_DATE]:
                    if not tr:
                        # ignore transactions with the empty label, see above
                        continue

                    if label == 'Investissement':
                        tr.amount = abs(tr.amount)
                    elif label == 'Désinvestissement':
                        tr.amount = -abs(tr.amount)
                    else:
                        assert False, 'unhandled line %s' % label
                    assert not any(len(cell) for cell in row[self.COL_LABEL + 1:]), 'there should be only the label'
                else:
                    if not tr:
                        continue

                    inv = Investment()
                    inv.label = label
                    inv.valuation = CleanDecimal(replace_dots=True).filter(row[self.COL_VALUATION])
                    if tr.amount < 0:
                        inv.valuation = -inv.valuation
                    inv.vdate = Date(dayfirst=True).filter(''.join(row[self.COL_DATE]))
                    tr.date = inv.vdate

                    inv.quantity = CleanDecimal(replace_dots=True, default=NotAvailable).filter(''.join(row[self.COL_QUANTITY]))
                    if inv.quantity and tr.amount < 0:
                        inv.quantity = -inv.quantity
                    inv.unitvalue = CleanDecimal(replace_dots=True, default=NotAvailable).filter(''.join(row[self.COL_UNITVALUE]))

                    tr.investments.append(inv)

        if tr:
            yield tr


class AdvisorPage(LoggedPage, MyHTMLPage):
    def is_profile_unavailable(self):
        return bool(self.doc.xpath(
            """//script[contains(text(), "Votre abonnement ne vous permet pas d'accéder")]"""
        ))

    @method
    class get_advisor(ItemElement):
        klass = Advisor

        def condition(self):
            return Field('name')(self)

        obj_name = CleanText('//div[label[contains(text(), "Votre conseiller")]]/span')
        obj_agency = CleanText('//div[label[contains(text(), "Votre agence")]]/span')
        obj_email = obj_mobile = NotAvailable

    @method
    class update_agency(ItemElement):
        obj_phone = CleanText('//div[label[contains(text(), "Téléphone")]]/span', replace=[('.', '')])
        obj_fax = CleanText('//div[label[contains(text(), "Fax")]]/span', replace=[('.', '')])
        obj_address = CleanText('//div[div[contains(text(), "Votre agence")]]/following-sibling::div[1]//div[not(label)]/span')

    def get_profile(self):
        profile = Person()

        # the name is only available in a welcome message. The messages can look like :
        # - Bienvenue M <first> <lastname> - <company name>
        # - Bienvenue M <firstname> <lastname>
        # - Bienvenue <company name>
        # We need to detect wether the company name is there, and where it begins.
        # relying on the dash only is dangerous as people may have dashes in their name and so may companies.
        # but we can detect company name from a dash between space
        # because we consider that impossible to be called jean - charles but only jean-charles
        welcome_msg = CleanText('//div[@id="BlcBienvenue"]/div[@class="btit"]')(self.doc)

        full_name_re = re.search(r'Bienvenue\s(((?! - ).)*)( - )?(.*)', welcome_msg)
        name_re = re.search(r'M(?:me|lle)? (.*)', full_name_re.group(1))

        profile.email = CleanText('//span[@id="fld8"]')(self.doc)

        if name_re:
            profile.name = name_re.group(1)
            if full_name_re.group(4):
                profile.company_name = full_name_re.group(4)
        else:
            profile.company_name = full_name_re.group(1)

        profile.email = CleanText('//span[contains(text(), "@")]')(self.doc)

        return profile


class TransactionDetailPage(LoggedPage, MyHTMLPage):
    def get_reference(self):
        return CleanText('//div[label[contains(text(), "Référence")]]//text()')(self.doc)


class LineboursePage(LoggedPage, HTMLPage):
    pass
