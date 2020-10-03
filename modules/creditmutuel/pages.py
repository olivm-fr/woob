# -*- coding: utf-8 -*-

# Copyright(C) 2010-2012 Julien Veyssier
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

import re
from hashlib import md5

from decimal import Decimal, InvalidOperation
from dateutil.relativedelta import relativedelta
from datetime import date, datetime
from random import randint
from collections import OrderedDict

from weboob.browser.pages import (
    HTMLPage, FormNotFound, LoggedPage, pagination,
    XMLPage, PartialHTMLPage, Page,
)
from weboob.browser.elements import ListElement, ItemElement, SkipItem, method, TableElement
from weboob.browser.filters.standard import (
    Filter, Env, CleanText, CleanDecimal, Field, Regexp, Async,
    AsyncLoad, Date, Format, Type, Currency, Base, Coalesce,
    Map, MapIn,
)
from weboob.browser.filters.html import Link, Attr, TableCell, ColumnNotFound
from weboob.exceptions import (
    BrowserIncorrectPassword, ParseError, ActionNeeded, BrowserUnavailable,
    AppValidation,
)
from weboob.capabilities import NotAvailable
from weboob.capabilities.base import empty, find_object
from weboob.capabilities.bank import (
    Account, Recipient, TransferBankError, Transfer,
    AddRecipientBankError, AddRecipientStep, Loan, Emitter,
)
from weboob.capabilities.wealth import (
    Investment, MarketOrder, MarketOrderDirection, MarketOrderType,
    MarketOrderPayment,
)
from weboob.capabilities.contact import Advisor
from weboob.capabilities.profile import Profile
from weboob.tools.capabilities.bank.iban import is_iban_valid
from weboob.tools.capabilities.bank.investments import IsinCode, IsinType
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.capabilities.bill import DocumentTypes, Subscription, Document
from weboob.tools.compat import urlparse, parse_qs, urljoin, range
from weboob.tools.date import parse_french_date, LinearDateGuesser
from weboob.tools.value import Value


def MyDecimal(*args, **kwargs):
    kwargs.update(replace_dots=True, default=NotAvailable)
    return CleanDecimal(*args, **kwargs)


def MyDate(*args, **kwargs):
    kwargs.update(dayfirst=True, default=NotAvailable)
    return Date(*args, **kwargs)


class UselessPage(LoggedPage, HTMLPage):
    pass


class RedirectPage(LoggedPage, HTMLPage):
    def on_load(self):
        super(RedirectPage, self).on_load()
        link = self.doc.xpath('//a[@id="P:F_1.R2:link"]')
        if link:
            self.browser.location(link[0].attrib['href'])


# PartialHTMLPage: this page may be used while redirecting, and so bear empty text
class NewHomePage(LoggedPage, PartialHTMLPage):
    def on_load(self):
        self.browser.is_new_website = True
        super(NewHomePage, self).on_load()


# PartialHTMLPage: this page may be used while redirecting, and so bear empty text
class LoginPage(PartialHTMLPage):
    REFRESH_MAX = 10.0

    def on_load(self):
        error_msg = CleanText('//div[contains(@class, "blocmsg err")] | //div[contains(@class, "blocmsg alerte")]')(self.doc)
        wrong_pass_msg = ('mot de passe est faux', 'mot de passe est révoqué', 'devez renseigner votre identifiant', "votre code d'accès n'est pas reconnu")
        action_needed_msg = ('pas autorisé à accéder à ce service', 'bloqué')
        website_unavailable_msg = ('service est temporairement interrompu', 'Problème technique')
        if any(msg in error_msg for msg in wrong_pass_msg):
            raise BrowserIncorrectPassword(error_msg)
        elif any(msg in error_msg for msg in action_needed_msg):
            raise ActionNeeded(error_msg)
        elif any(msg in error_msg for msg in website_unavailable_msg):
            raise BrowserUnavailable(error_msg)
        elif 'précédente connexion a expiré' in error_msg:
            # On occasions, login upon resyncing throws: 'Votre précédente connexion
            # a expiré. Merci de bien vouloir vous identifier à nouveau.'
            self.logger.warning('Restarting connection because it expired')
            return
        elif 'antivirus' in error_msg.lower():
            self.logger.warning("This error message doesn't impact the success of the connection %s", error_msg)
            return
        assert not error_msg, "Unhandled error: '%s'" % error_msg

    def login(self, login, passwd, redirect=False):
        form = self.get_form(xpath='//form[contains(@name, "ident")]')
        # format login/password like login/password sent by firefox or chromium browser
        form['_cm_user'] = login
        form['_cm_pwd'] = passwd
        form.submit(allow_redirects=redirect)

    @property
    def logged(self):
        return self.doc.xpath('//div[@id="e_identification_ok"]')


class LoginErrorPage(HTMLPage):
    def on_load(self):
        raise BrowserIncorrectPassword(CleanText('//div[has-class("blocmsg")]')(self.doc))


class FiscalityConfirmationPage(LoggedPage, HTMLPage):
    pass


class AppValidationPage(Page):
    def get_validation_msg(self):
        # ex: "Une demande de confirmation mobile a été transmise à votre appareil "SuperPhone de Toto". Démarrez votre application mobile Crédit Mutuel pour vérifier et confirmer cette opération."
        return CleanText('//div[@id="inMobileAppMessage"]//h2[not(img)]')(self.doc)

    def get_polling_id(self):
        return Regexp(CleanText('//script[contains(text(), "transactionId")]'), r"transactionId: '(.{49})', get")(self.doc)

    def get_polling_data(self, form_xpath='//form'):
        form = self.get_form(form_xpath)
        data = {
            'polling_id': self.get_polling_id(),
            'final_url': form.url,
            # Need to convert form into dict, pickling during dump_state() with boobank doesn't work
            'final_url_params': dict(form.items()),
        }
        return data


# PartialHTMLPage: this page shares URL with other pages,
# and might be empty of text while used in a redirection
class MobileConfirmationPage(PartialHTMLPage, AppValidationPage):
    def is_here(self):
        return (
            'Démarrez votre application mobile' in CleanText('//div[contains(@id, "inMobileAppMessage")]')(self.doc)
            or 'demande de confirmation mobile' in CleanText('//div[contains(@id, "inMobileAppMessage")]')(self.doc)
            or (
                'Authentification forte' in CleanText('//p[contains(@id, "title")]')(self.doc)
                and CleanText('//*[contains(text(), "Confirmer mon identité")]')(self.doc)
            )
        )

    def skip_redo_twofa(self):
        # Handle reconnection messages of the form "Si vous préférez confirmer
        # votre identité plus tard : [cliquez ici].".

        # Since there are a few DOM nodes between the link and the actual text,
        # we can't use a plain Link here.
        # This is feeble, but oh well.
        link = Attr('//li[contains(text(), "confirmer votre identité plus tard")]//a[contains(@href, "Bypass") and contains(text(), "cliquez ici")]', 'href', default=None)(self.doc)
        if link:
            self.logger.warning("2FA is still valid, avoiding the 'confirm your identity' page.")
            self.browser.location(link)

    # We land on this page for some connections, but can still bypass this verification for now
    def check_bypass(self):
        link = Attr('//a[contains(text(), "Accéder à mon Espace Client sans Confirmation Mobile") or contains(text(), "accéder à votre espace client")]', 'href', default=None)(self.doc)
        if link:
            self.logger.warning('This connexion is bypassing mobile confirmation')
            self.browser.location(link)
        else:
            self.logger.warning('This connexion cannot bypass mobile confirmation')


class TwoFAUnabledPage(PartialHTMLPage):
    def is_here(self):
        return self.doc.xpath('//*[contains(text(), "aucun moyen pour confirmer")]')

    def get_error_msg(self):
        return CleanText('//*[contains(text(), "aucun moyen pour confirmer")]')(self.doc)


class DecoupledStatePage(XMLPage):
    def get_decoupled_state(self):
        return CleanText('//transactionState')(self.doc)


class CancelDecoupled(HTMLPage):
    pass


# PartialHTMLPage: this page shares URL with other pages,
# and might be empty of text while used in a redirection
class OtpValidationPage(PartialHTMLPage):
    def is_here(self):
        return 'envoyé par SMS' in CleanText('//div[contains(@id, "OTPDeliveryChannelText")]')(self.doc)

    def get_message(self):
        # Ex: 'Un code de confirmation vient de vous être envoyé par SMS au 06 XX XX X1 23, le jeudi 26 décembre 2019 à 18:12:56.'
        return Regexp(CleanText('//div[contains(@id, "OTPDeliveryChannelText")]'), r'(.+\d{2}), le')(self.doc)

    def get_error_message(self):
        return CleanText('//div[contains(@class, "bloctxt err")]')(self.doc)

    def get_otp_data(self):
        form = self.get_form()
        data = {
            'final_url': form.url,
            # Need to convert form into dict, pickling during dump_state() with boobank doesn't work
            'final_url_params': dict(form.items()),
        }
        return data


# PartialHTMLPage: this page shares URL with other pages,
# and might be empty of text while used in a redirection
class OtpBlockedErrorPage(PartialHTMLPage):
    def is_here(self):
        return 'temporairement bloqué' in CleanText('//div[contains(@class, "bloctxt err")]')(self.doc)

    def get_error_message(self):
        return CleanText('//div[contains(@class, "bloctxt err")]')(self.doc)


class EmptyPage(LoggedPage, HTMLPage):
    REFRESH_MAX = 10.0

    def on_load(self):
        # Action needed message is like "Votre Carte de Clés Personnelles numéro 3 est révoquée."
        # or "Avant de passer toute opération sur ce site, nous vous invitons à prendre
        # connaissance de l'information générale sur la bourse et les marchés financiers."
        action_needed = (
            CleanText('//p[contains(text(), "Votre Carte de Clés Personnelles") and contains(text(), "est révoquée")]')(self.doc)
            or CleanText('//p[contains(text(), "Avant de passer toute opération sur ce site")]')(self.doc)
        )
        if action_needed:
            raise ActionNeeded(action_needed)
        maintenance = CleanText('//td[@class="ALERTE"]/p/span[contains(text(), "Dans le cadre de l\'amélioration de nos services, nous vous informons que le service est interrompu")]')(self.doc)
        if maintenance:
            raise BrowserUnavailable(maintenance)


class UserSpacePage(LoggedPage, HTMLPage):
    def on_load(self):
        if self.doc.xpath('//form[@id="GoValider"]'):
            raise ActionNeeded("Le site du contrat Banque à Distance a besoin d'informations supplémentaires")
        personal_infos = CleanText('//form[@class="_devb_act ___Form"]//div[contains(@class, "bloctxt")]/p[1]')(self.doc)
        if 'Afin de compléter vos informations personnelles, renseignez le formulaire ci-dessous' in personal_infos:
            raise ActionNeeded("Le site nécessite la saisie des informations personnelles de l'utilisateur.")

        super(UserSpacePage, self).on_load()


class ChangePasswordPage(LoggedPage, HTMLPage):
    def on_load(self):
        raise BrowserIncorrectPassword('Please change your password')


class item_account_generic(ItemElement):
    klass = Account

    TYPES = OrderedDict([
        (re.compile(r'Credits Promoteurs'), Account.TYPE_CHECKING),  # it doesn't fit loan's model
        (re.compile(r'Compte Cheque'), Account.TYPE_CHECKING),
        (re.compile(r'Comptes? Courants?'), Account.TYPE_CHECKING),
        (re.compile(r'Cpte Courant'), Account.TYPE_CHECKING),
        (re.compile(r'Contrat Personnel'), Account.TYPE_CHECKING),
        (re.compile(r'Cc Contrat Personnel'), Account.TYPE_CHECKING),
        (re.compile(r'C/C'), Account.TYPE_CHECKING),
        (re.compile(r'Start\b'), Account.TYPE_CHECKING),
        (re.compile(r'Comptes courants'), Account.TYPE_CHECKING),
        (re.compile(r'Service Accueil'), Account.TYPE_CHECKING),
        (re.compile(r'Eurocompte Serenite'), Account.TYPE_CHECKING),
        (re.compile(r'Eurocompte Confort'), Account.TYPE_CHECKING),
        (re.compile(r'Compte Service Bancaire De Base'), Account.TYPE_CHECKING),
        (re.compile(r'Catip\b'), Account.TYPE_DEPOSIT),
        (re.compile(r'Cic Immo'), Account.TYPE_MORTGAGE),
        (re.compile(r'Credit'), Account.TYPE_LOAN),
        (re.compile(r'Crédits'), Account.TYPE_LOAN),
        (re.compile(r'Eco-Prêt'), Account.TYPE_LOAN),
        (re.compile(r'Mcne'), Account.TYPE_LOAN),
        (re.compile(r'Nouveau Prêt'), Account.TYPE_LOAN),
        (re.compile(r'Pr[eê]t\b'), Account.TYPE_LOAN),
        (re.compile(r'Regroupement De Credits'), Account.TYPE_LOAN),
        (re.compile(r'Nouveau Pret 0%'), Account.TYPE_LOAN),
        (re.compile(r'Global Auto'), Account.TYPE_LOAN),
        (re.compile(r'Passeport Credit'), Account.TYPE_REVOLVING_CREDIT),
        (re.compile(r'Allure\b'), Account.TYPE_REVOLVING_CREDIT),  # 'Allure Libre' or 'credit Allure'
        (re.compile(r'Preference'), Account.TYPE_REVOLVING_CREDIT),
        (re.compile(r'Plan 4'), Account.TYPE_REVOLVING_CREDIT),
        (re.compile(r'P.E.A'), Account.TYPE_PEA),
        (re.compile(r'Pea\b'), Account.TYPE_PEA),
        (re.compile(r'Compte De Liquidite Pea'), Account.TYPE_PEA),
        (re.compile(r'Compte Epargne'), Account.TYPE_SAVINGS),
        (re.compile(r'Etalis'), Account.TYPE_SAVINGS),
        (re.compile(r'Ldd'), Account.TYPE_SAVINGS),
        (re.compile(r'Livret'), Account.TYPE_SAVINGS),
        (re.compile(r"Plan D'Epargne"), Account.TYPE_SAVINGS),
        (re.compile(r'Tonic Crois'), Account.TYPE_SAVINGS),  # eg: 'Tonic Croissance', 'Tonic Crois Pro'
        (re.compile(r'Tonic Societaire'), Account.TYPE_SAVINGS),
        (re.compile(r'Capital Expansion'), Account.TYPE_SAVINGS),
        (re.compile(r'Épargne'), Account.TYPE_SAVINGS),
        (re.compile(r'Capital Plus'), Account.TYPE_SAVINGS),
        (re.compile(r'Pep\b'), Account.TYPE_SAVINGS),
        (re.compile(r'Compte Duo'), Account.TYPE_SAVINGS),
        (re.compile(r'Compte Garantie Titres'), Account.TYPE_MARKET),
        (re.compile(r'Ppe'), Account.TYPE_LOAN),
        (re.compile(r'P.(C.)?A.S.'), Account.TYPE_LOAN),
        (re.compile(r'Demarrimo'), Account.TYPE_MORTGAGE),
        (re.compile(r'Permis.*Jour'), Account.TYPE_LOAN),
        (re.compile(r'Esp[èe]ce Gages?\b'), Account.TYPE_CHECKING),  # ex : Compte Gere Espece Gage M...
    ])

    REVOLVING_LOAN_REGEXES = [
        re.compile(r'Passeport Credit'),
        re.compile(r'Allure'),
        re.compile(r'Preference'),
        re.compile(r'Plan 4'),
        re.compile(r'Credit En Reserve'),
    ]

    def condition(self):
        if len(self.el.xpath('./td')) < 2:
            return False

        first_td = self.el.xpath('./td')[0]

        return (("i" in first_td.attrib.get('class', '') or "p" in first_td.attrib.get('class', ''))
                and (first_td.find('a') is not None or (first_td.find('.//span') is not None
                and "cartes" in first_td.findtext('.//span') and first_td.find('./div/a') is not None)))

    def loan_condition(self, check_no_details=False):
        _type = Field('type')(self)
        label = Field('label')(self)
        # The 'lien_inter_sites' link leads to a 404 and is not a link to loans details.
        # The link name on the website is : Vos encours mobilisation de créances
        details_link = Link('.//a[not(contains(@href, "lien_inter_sites"))]', default=None)(self)

        # mobile accounts are leading to a 404 error when parsing history
        # furthermore this is not exactly a loan account
        if re.search(r'Le Mobile +([0-9]{2} ?){5}', label):
            return False

        if (
            details_link and
            item_account_generic.condition and
            _type in (Account.TYPE_LOAN, Account.TYPE_MORTGAGE) and
            not self.is_revolving(label)
        ):
            details = self.page.browser.open(details_link).page
            if details and 'cloturé' not in CleanText('//form[@id="P:F"]//div[@class="blocmsg info"]//p')(details.doc):
                fiche_details = CleanText('//table[@class="fiche"]')(details.doc)
                if check_no_details:  # check_no_details is used to determine if condition should check the absence of details, otherwise we still check the presence of details
                    return not fiche_details
                return fiche_details
        return False

    class Label(Filter):
        def filter(self, text):
            return text.lstrip(' 0123456789').title()

    class Type(Filter):
        def filter(self, label):
            for regex, actype in item_account_generic.TYPES.items():
                if regex.search(label):
                    return actype
            return Account.TYPE_UNKNOWN

    obj_id = Env('id')
    obj_number = Env('id')
    obj_label = Label(CleanText('./td[1]/a/text() | ./td[1]/a/span[@class and not(contains(@class, "doux"))] | ./td[1]/div/a[has-class("cb")]'))
    obj_coming = Env('coming')
    obj_balance = Env('balance')
    obj_currency = FrenchTransaction.Currency('./td[2] | ./td[3]')
    obj__card_links = []

    def obj__link_id(self):
        if self.is_revolving(Field('label')(self)):
            page = self.page.browser.open(Link('./td[1]//a')(self)).page
            if page and page.doc.xpath('//div[@class="fg"]/a[contains(@href, "%s")]' % Field('id')(self)):
                return urljoin(page.url, Link('//div[@class="fg"]/a')(page.doc))
        return Link('./td[1]//a')(self)

    def obj_type(self):
        t = self.Type(Field('label'))(self)
        # sometimes, using the label is not enough to infer the account's type.
        # this is a fallback that uses the account's group label
        if t == 0:
            return self.Type(CleanText('./preceding-sibling::tr/th[contains(@class, "rupture eir_tblshowth")][1]'))(self)
        return t

    obj__is_inv = False
    obj__is_webid = Env('_is_webid')

    def parse(self, el):
        accounting = None
        coming = None
        page = None
        link = el.xpath('./td[1]//a')[0].get('href', '')
        if 'POR_SyntheseLst' in link:
            raise SkipItem()

        url = urlparse(link)
        p = parse_qs(url.query)
        if 'rib' not in p and 'webid' not in p:
            raise SkipItem()

        for td in el.xpath('./td[2] | ./td[3]'):
            try:
                balance = CleanDecimal('.', replace_dots=True)(td)
                has_child_def_card = CleanText('.//following-sibling::tr[1]//span[contains(text(), "Dépenses cartes prélevées")]')(el)
                if Field('type')(self) == Account.TYPE_CHECKING and not has_child_def_card:
                    # the present day, real balance (without comings) is displayed in the operations page of the account
                    # need to limit requests to checking accounts with no def cards
                    details_page_link = Link('.//a', default=None)(self)
                    if details_page_link:
                        coming_page = self.page.browser.open(details_page_link).page
                        balance_without_comings = coming_page.get_balance()
                        if not empty(balance_without_comings):
                            balance = balance_without_comings
            except InvalidOperation:
                continue
            else:
                break
        else:
            if 'lien_inter_sites' in link:
                raise SkipItem()
            else:
                raise ParseError('Unable to find balance for account %s' % CleanText('./td[1]/a')(el))

        self.env['_is_webid'] = False

        if "cartes" in CleanText('./td[1]')(el):
            # handle cb deferred card
            if "cartes" in CleanText('./preceding-sibling::tr[1]/td[1]', replace=[(' ', '')])(el):
                # In case it's the second month of card history present, we need to ignore the first
                # one to get the attach accoount
                id_xpath = './preceding-sibling::tr[2]/td[1]/a/node()[contains(@class, "doux")]'
            else:
                # first month of history, the previous tr is the attached account
                id_xpath = './preceding-sibling::tr[1]/td[1]/a/node()[contains(@class, "doux")]'
        else:
            # classical account
            id_xpath = './td[1]/a/node()[contains(@class, "doux")]'

        _id = CleanText(id_xpath, replace=[(' ', '')])(el)
        if not _id:
            if 'rib' in p:
                _id = p['rib'][0]
            else:
                _id = p['webid'][0]
                self.env['_is_webid'] = True

        if self.is_revolving(Field('label')(self)):
            page = self.page.browser.open(link).page
            if isinstance(page, RevolvingLoansList):
                # some revolving loans are listed on an other page. On the accountList, there is
                # just a link for this page, that's why we don't handle it here
                raise SkipItem()

        # Handle cards
        if _id in self.parent.objects:
            if not page:
                page = self.page.browser.open(link).page
            # Handle real balances
            coming = page.find_amount("Opérations à venir") if page else None
            accounting = page.find_amount("Solde comptable") if page else None
            # on old website we want card's history in account's history
            if not page.browser.is_new_website:
                self.logger.info('On old creditmutuel website')
                account = self.parent.objects[_id]
                if not account.coming:
                    account.coming = Decimal('0.0')
                # Get end of month
                date = parse_french_date(Regexp(Field('label'), r'Fin (.+) (\d{4})', '01 \\1 \\2')(self)) + relativedelta(day=31)
                if date > datetime.now() - relativedelta(day=1):
                    account.coming += balance
                account._card_links.append(link)
            else:
                multiple_cards_xpath = '//select[@name="Data_SelectedCardItemKey"]/option[contains(text(),"Carte")]'
                single_card_xpath = '//span[has-class("_c1 fg _c1")]'
                card_xpath = multiple_cards_xpath + ' | ' + single_card_xpath
                for elem in page.doc.xpath(card_xpath):
                    card_id = Regexp(CleanText('.', symbols=' '), r'([\dx]{16})')(elem)
                    is_in_accounts = any(card_id in a.id for a in page.browser.accounts_list)
                    if card_id in self.page.browser.unavailablecards or is_in_accounts:
                        continue

                    card = Account()
                    card.type = Account.TYPE_CARD
                    card.id = card.number = card_id
                    card._link_id = link
                    card._is_inv = card._is_webid = False
                    card.parent = self.parent.objects[_id]

                    pattern = r'Carte\s(\w+).*\d{4}\s([A-Za-z\s]+)(.*)'
                    m = re.search(pattern, CleanText('.')(elem))
                    card.label = "%s %s %s" % (m.group(1), card_id, m.group(2))
                    card.balance = Decimal('0.0')
                    card.currency = card.get_currency(m.group(3))
                    card._card_pages = [page]
                    card.coming = Decimal('0.0')
                    #handling the case were the month is the coming one. There won't be next_month here.
                    date = parse_french_date(Regexp(Field('label'), r'Fin (.+) (\d{4})', '01 \\1 \\2')(self)) + relativedelta(day=31)
                    if date > datetime.now() - relativedelta(day=1):
                        card.coming = CleanDecimal(replace_dots=True).filter(m.group(3))
                    next_month = Link('./following-sibling::tr[contains(@class, "encours")][1]/td[1]//a', default=None)(self)
                    if next_month:
                        card_page = page.browser.open(next_month).page
                        for e in card_page.doc.xpath(card_xpath):
                            if card.id == Regexp(CleanText('.', symbols=' '), r'([\dx]{16})')(e):
                                m = re.search(pattern, CleanText('.')(e))
                                card._card_pages.append(card_page)
                                card.coming += CleanDecimal(replace_dots=True).filter(m.group(3))
                                break

                    self.page.browser.accounts_list.append(card)

            raise SkipItem()

        self.env['id'] = _id

        if accounting is not None and accounting + (coming or Decimal('0')) != balance:
            self.page.logger.warning('%s + %s != %s' % (accounting, coming, balance))

        if accounting is not None:
            balance = accounting

        self.env['balance'] = balance
        self.env['coming'] = coming or NotAvailable

    def is_revolving(self, label):
        return (any(revolving_loan_regex.search(label)
                    for revolving_loan_regex in item_account_generic.REVOLVING_LOAN_REGEXES)
                or label.lower() in self.page.browser.revolving_accounts)


class AccountsPage(LoggedPage, HTMLPage):
    def has_no_account(self):
        return CleanText('//td[contains(text(), "Votre contrat de banque à distance ne vous donne accès à aucun compte.")]')(self.doc)

    @method
    class iter_accounts(ListElement):
        item_xpath = '//div[has-class("a_blocappli")]//tr'
        flush_at_end = True

        class item_account(item_account_generic):
            def condition(self):
                _type = Field('type')(self)
                if 'Valorisation Totale De Vos Portefeuilles Titres' in Field('label')(self):
                    return False
                return item_account_generic.condition(self) and _type not in (Account.TYPE_LOAN, Account.TYPE_MORTGAGE)

        class item_loan_low_details(item_account_generic):
            klass = Loan

            def condition(self):
                return item_account_generic.loan_condition(self, check_no_details=True)

            obj__parent_id = NotAvailable

        class item_loan(item_account_generic):
            klass = Loan

            load_details = Link('.//a') & AsyncLoad

            def condition(self):
                return item_account_generic.loan_condition(self)

            obj_total_amount = Async('details') & MyDecimal('//div[@id="F4:expContent"]/table/tbody/tr[1]/td[1]/text()')
            obj_rate = Async('details') & MyDecimal('//div[@id="F4:expContent"]/table/tbody/tr[2]/td[1]')
            obj_nb_payments_left = Async('details') & Type(CleanText(
                '//div[@id="F4:expContent"]/table/tbody/tr[2]/td[2]/text()'), type=int, default=NotAvailable)
            obj_subscription_date = Async('details') & MyDate(Regexp(CleanText(
                '//*[@id="F4:expContent"]/table/tbody/tr[1]/th[1]'), r' (\d{2}/\d{2}/\d{4})', default=NotAvailable))
            obj_maturity_date = Async('details') & MyDate(
                CleanText('//div[@id="F4:expContent"]/table/tbody/tr[4]/td[2]'))

            obj_next_payment_amount = Async('details') & MyDecimal('//div[@id="F4:expContent"]/table/tbody/tr[3]/td[2]')
            obj_next_payment_date = Async('details') & MyDate(
                CleanText('//div[@id="F4:expContent"]/table/tbody/tr[3]/td[1]'))

            obj_last_payment_amount = Async('details') & MyDecimal('//td[@id="F2_0.T12"]')
            obj_last_payment_date = (Async('details') &
                MyDate(CleanText('//div[@id="F8:expContent"]/table/tbody/tr[1]/td[1]')))

            def obj__parent_id(self):
                return Async('details').loaded_page(self).get_parent_id()

        class item_revolving_loan(item_account_generic):
            klass = Loan

            load_details = Link('.//a') & AsyncLoad

            obj_total_amount = Async('details') & MyDecimal('//main[@id="ei_tpl_content"]/div/div[2]/table/tbody/tr/td[3]')
            obj_type = Account.TYPE_REVOLVING_CREDIT

            def obj_used_amount(self):
                return -Field('balance')(self)

            def condition(self):
                label = Field('label')(self)
                return (
                    item_account_generic.condition(self)
                    and Field('type')(self) == Account.TYPE_REVOLVING_CREDIT
                    and self.is_revolving(label)
                )

    def get_advisor_link(self):
        return Link('//div[@id="e_conseiller"]/a', default=None)(self.doc)

    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = CleanText('//div[@id="e_conseiller"]/a')

    @method
    class get_profile(ItemElement):
        klass = Profile

        obj_name = CleanText('//div[@id="e_identification_ok_content"]//strong[1]')


class NewAccountsPage(NewHomePage, AccountsPage):
    def get_agency(self):
        return Regexp(CleanText('//script[contains(text(), "lien_caisse")]', default=''),
                      r'(https://[^"]+)', default='')(self.doc)

    @method
    class get_advisor(ItemElement):
        klass = Advisor

        obj_name = Regexp(CleanText('//script[contains(text(), "Espace Conseiller")]'),
                          r'consname.+?([\w\s]+)')

    @method
    class get_profile(ItemElement):
        klass = Profile

        obj_name = CleanText('//p[contains(@class, "master_nom")]')


class AdvisorPage(LoggedPage, HTMLPage):
    @method
    class update_advisor(ItemElement):
        obj_email = CleanText('//table//*[@itemprop="email"]')
        obj_phone = CleanText('//table//*[@itemprop="telephone"]', replace=[(' ', '')])
        obj_mobile = NotAvailable
        obj_fax = CleanText('//table//*[@itemprop="faxNumber"]', replace=[(' ', '')])
        obj_agency = CleanText('//div/*[@itemprop="name"]')
        obj_address = Format('%s %s %s', CleanText('//table//*[@itemprop="streetAddress"]'),
                                         CleanText('//table//*[@itemprop="postalCode"]'),
                                         CleanText('//table//*[@itemprop="addressLocality"]'))


class CardsActivityPage(LoggedPage, HTMLPage):
    def companies_link(self):
        companies_link = []
        for tr in self.doc.xpath('//table[@summary="Liste des titulaires de contrats cartes"]//tr'):
            companies_link.append(Link(tr.xpath('.//a'))(self))
        return companies_link


class Pagination(object):
    def next_page(self):
        try:
            form = self.page.get_form('//form[@id="paginationForm" or @id="frmSTARCpag"]')
        except FormNotFound:
            return self.next_month()

        text = CleanText.clean(form.el)
        m = re.search(r'(\d+)/(\d+)', text or '', flags=re.MULTILINE)
        if not m:
            return self.next_month()

        cur = int(m.group(1))
        last = int(m.group(2))

        if cur == last:
            return self.next_month()

        form['imgOpePagSui.x'] = randint(1, 29)
        form['imgOpePagSui.y'] = randint(1, 17)

        form['page'] = str(cur + 1)
        return form.request

    def next_month(self):
        try:
            form = self.page.get_form('//form[@id="frmStarcLstOpe"]')
        except FormNotFound:
            return

        try:
            form['moi'] = self.page.doc.xpath('//select[@id="moi"]/option[@selected]/following-sibling::option')[0].attrib['value']
            form['page'] = 1
        except IndexError:
            return

        return form.request


class CardsListPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class iter_cards(TableElement):
        item_xpath = '//table[has-class("liste")]/tbody/tr'
        head_xpath = '//table[has-class("liste")]/thead//tr/th'

        col_owner = 'Porteur'
        col_card = 'Carte'

        def next_page(self):
            try:
                form = self.page.get_form('//form[contains(@id, "frmStarcLstCtrPag")]')
                form['imgCtrPagSui.x'] =  randint(1, 29)
                form['imgCtrPagSui.y'] =  randint(1, 17)
                m = re.search(r'(\d+)/(\d+)', CleanText('.')(form.el))
                if m and int(m.group(1)) < int(m.group(2)):
                    return form.request
            except FormNotFound:
                return

        class item(ItemElement):
            klass = Account

            obj_number = Env('number', default='')
            obj_id = Format(
                '%s%s',
                Field('number'),
                Field('_ctr'),
            )
            obj_label = Format(
                '%s %s %s',
                CleanText(TableCell('card')),
                Field('number'),
                CleanText(TableCell('owner')),
            )
            obj_coming = CleanDecimal(
                './td[@class="i d" or @class="p d"][2]',
                replace_dots=True,
                default=NotAvailable,
            )
            obj_balance = Decimal('0.00')
            obj_currency = FrenchTransaction.Currency(CleanText('./td[small][1]'))

            obj_type = Account.TYPE_CARD
            obj__card_pages = Env('page')
            obj__is_inv = False
            obj__is_webid = False
            obj__ctr = Field('_link_id') & Regexp(pattern=r'ctr=(\d+)')

            def obj__pre_link(self):
                return self.page.url

            def obj__link_id(self):
                return Link(TableCell('card')(self)[0].xpath('./a'))(self)

            def parse(self, el):
                page = self.page.browser.open(Field('_link_id')(self)).page
                self.env['page'] = [page]

                if len(page.doc.xpath('//caption[contains(text(), "débits immédiats")]')):
                    raise SkipItem()

                # Handle multi cards
                options = page.doc.xpath('//select[@id="iso"]/option')
                for option in options:
                    card = Account()
                    card_list_page = page.browser.open(Link('//form//a[text()="Contrat"]', default=None)(page.doc)).page
                    xpath = '//table[has-class("liste")]/tbody/tr'
                    active_card = CleanText('%s[td[text()="Active"]][1]/td[2]' % xpath, replace=[(' ', '')], default=None)(card_list_page.doc)
                    number = CleanText('.', replace=[(' ', '')])(option)
                    if active_card == number:
                        for attr in self._attrs:
                            self.handle_attr(attr, getattr(self, 'obj_%s' % attr))
                            setattr(card, attr, getattr(self.obj, attr))

                        card.id = number + card._ctr
                        card.number = number
                        card.label = card.label.replace('  ', ' %s ' % number)
                        card2 = find_object(self.page.browser.cards_list, id=card.id[:16])
                        if card2:
                            card._link_id = card2._link_id
                            card._parent_id = card2._parent_id
                            card.coming = card2.coming
                            card._referer = card2._referer
                            card._secondpage = card2._secondpage
                            self.page.browser.accounts_list.remove(card2)
                        self.page.browser.accounts_list.append(card)
                        self.page.browser.cards_list.append(card)

                # Skip multi and expired cards
                if len(options) or len(page.doc.xpath('//span[@id="ERREUR"]')):
                    raise SkipItem()

                # 1 card : we have to check on another page to get id
                page = page.browser.open(Link('//form//a[text()="Contrat"]', default=None)(page.doc)).page
                xpath = '//table[has-class("liste")]/tbody/tr'
                active_card = CleanText('%s[td[text()="Active"]][1]/td[2]' % xpath, replace=[(' ', '')], default=None)(page.doc)
                for cards in page.doc.xpath(xpath):
                    if CleanText(cards.xpath('./td[1]'))(self) != 'Active':
                        self.page.browser.unavailablecards.append(CleanText(cards.xpath('./td[2]'), replace=[(' ', '')])(self))

                if not active_card and len(page.doc.xpath(xpath)) != 1:
                    raise SkipItem()

                self.env['number'] = active_card or CleanText('%s[1]/td[2]' % xpath, replace=[(' ', '')])(page.doc)


class Transaction(FrenchTransaction):
    PATTERNS = [
        (re.compile(r'^(VIR(EMENT)?|VIRT.) (?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^(PRLV|Plt|PRELEVEMENT) (?P<text>.*)'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^(?P<text>.*)\s?(CARTE |PAYWEB)?\d+ PAIEMENT CB\s+(?P<dd>\d{2})(?P<mm>\d{2}) ?(.*)$'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^PAIEMENT PSC\s+(?P<dd>\d{2})(?P<mm>\d{2}) (?P<text>.*) CARTE \d+ ?(.*)$'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^Regroupement \d+ PAIEMENTS (?P<dd>\d{2})(?P<mm>\d{2}) (?P<text>.*) CARTE \d+ ?(.*)$'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^(?P<text>RELEVE CARTE.*)'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^RETRAIT DAB (?P<dd>\d{2})(?P<mm>\d{2}) (?P<text>.*) CARTE [\*\d]+'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^(?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{4}) RETRAIT DAB (?P<text>.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^CHEQUE( (?P<text>.*))?$'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(F )?COTIS\.? (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(REMISE|REM CHQ) (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>(ÉCHÉANCE|Echéance)).*'), FrenchTransaction.TYPE_LOAN_PAYMENT),
    ]

    _is_coming = False


class OperationsPage(LoggedPage, HTMLPage):
    def go_on_history_tab(self):
        try:
            # Maybe obsolete
            form = self.get_form(id='I1:fm')
        except FormNotFound:
            form = self.get_form(id='I1:P:F')
        form['_FID_DoShowListView'] = ''
        form.submit()

    @method
    class get_history(Pagination, Transaction.TransactionsElement):
        head_xpath = '//table[has-class("liste")]//thead//tr/th'
        item_xpath = '//table[has-class("liste")]//tbody/tr'

        class item(Transaction.TransactionElement):
            def condition(self):
                return (
                    len(self.el.xpath('./td')) >= 3
                    and len(self.el.xpath('./td[@class="i g" or @class="p g" or contains(@class, "_c1")]')) > 0
                )

            class OwnRaw(Filter):
                def __call__(self, item):
                    el = TableCell('raw')(item)[0]

                    # All the sub-elements with the class "eir_showxs" are to
                    # be shown only in mobile screens, and are hidden via
                    # display:none on desktop.
                    # Clear their content in place.
                    for elem in el.xpath(".//node()[contains(@class, \"eir_showxs\")]"):
                        elem.drop_tree()

                    # Remove hidden parts of labels:
                    # hideifscript: Date de valeur XX/XX/XXXX
                    # fd: Avis d'opéré
                    # survey to add other regx
                    parts = (re.sub(r'Détail|Date de valeur\s+:\s+\d{2}/\d{2}(/\d{4})?', '', txt.strip())
                             for txt in el.itertext() if txt.strip())
                    # Removing empty strings
                    parts = list(filter(bool, parts))

                    # Some transactions have no label
                    if not parts:
                        return NotAvailable

                    # To simplify categorization of CB, reverse order of parts to separate
                    # location and institution
                    detail = "Cliquer pour déplier ou plier le détail de l'opération"
                    if detail in parts:
                        parts.remove(detail)
                    if parts[0].startswith('PAIEMENT CB'):
                        parts.reverse()

                    return ' '.join(parts)

            def obj_raw(self):
                own_raw = self.OwnRaw()(self)
                if empty(own_raw):
                    return NotAvailable
                return Transaction.Raw(self.OwnRaw())(self)

    def find_amount(self, title):
        try:
            td = self.doc.xpath('//th[contains(text(), $title)]/../td', title=title)[0]
        except IndexError:
            return None
        else:
            return Decimal(FrenchTransaction.clean_amount(td.text))

    def get_coming_link(self):
        try:
            a = self.doc.xpath('//a[contains(text(), "Opérations à venir")]')[0]
        except IndexError:
            return None
        else:
            return a.attrib['href']

    def has_more_operations(self):
        return bool(self.doc.xpath('//a/span[contains(text(), "Plus d\'opérations")]'))

    def get_balance(self):
        return CleanDecimal.French('//span[contains(text(), "Dont opérations enregistrées")]', default=NotAvailable)(self.doc)

    def get_parent_id(self):
        # There are 5 numbers that we don't want before the real id
        # "12345 01200 000123456798" => "01200000123456798"
        return Regexp(
            CleanText(
                '//div[@id="F4:expContent"]/table/tbody/tr[1]/td[2]',
                replace=[(' ', '')]
            ),
            r'\d{5}(\d+)',
            default=NotAvailable,
        )(self.doc)


class LoansOperationsPage(OperationsPage):
    @method
    class get_history(Pagination, Transaction.TransactionsElement):
        head_xpath = '//table[has-class("liste")]/thead/tr/th'
        item_xpath = '//table[has-class("liste")]/tr'

        class item(Transaction.TransactionElement):
            def condition(self):
                return (
                    len(self.el.xpath('./td')) >= 3
                    and len(self.el.xpath('./td[@class="i g" or @class="p g" or contains(@class, "_c1")]')) > 0
                    and 'Echéance' in CleanText(TableCell('raw'))(self)
                    and 'Intérêts' in CleanText(TableCell('raw'))(self)
                )

            # Crédit = Echéance / Débit = Intérêts (and Assurance sometimes)
            # 'Intérêts' do not affect the loans value.
            obj_gross_amount = CleanDecimal.French(TableCell('credit'))

            # Need to set it manually to NotAvailable otherwise Transaction.TransactionElement
            # set its value to TableCell('credit')
            obj_amount = NotAvailable

            def obj_commission(self):
                raw = Field('raw')(self)
                if 'Assurance' in raw and 'Intérêts' in raw:
                    # There is 2 values in the 'debit' TableCell if we have
                    # Assurance and Intérêts...
                    interets, assurance = Regexp(CleanText(TableCell('debit')), r'([\d, ]+)', r'\1', nth='*')(self)
                    return (
                        CleanDecimal.French(sign='-').filter(interets)
                        - CleanDecimal.French().filter(assurance)
                    )
                return CleanDecimal.French(TableCell('debit'), sign='-')(self)


class CardsOpePage(OperationsPage):
    def select_card(self, card_number):
        if CleanText('//select[@id="iso"]', default=None)(self.doc):
            form = self.get_form('//p[has-class("restriction")]')
            card_number = ' '.join([card_number[j*4:j*4+4] for j in range(len(card_number)//4+1)]).strip()
            form['iso'] = Attr('//option[text()="%s"]' % card_number, 'value')(self.doc)
            moi = Attr('//select[@id="moi"]/option[@selected]', 'value', default=None)(self.doc)
            if moi:
                form['moi'] = moi
            return self.browser.open(form.url, data=dict(form)).page
        return self

    @method
    class get_history(Pagination, Transaction.TransactionsElement):
        head_xpath = '//table[has-class("liste")]//thead//tr/th'
        item_xpath = '//table[has-class("liste")]/tr'

        col_city = 'Ville'
        col_original_amount = "Montant d'origine"
        col_amount = 'Montant'

        class item(Transaction.TransactionElement):
            condition = lambda self: len(self.el.xpath('./td')) >= 5

            obj_raw = obj_label = Format('%s %s', TableCell('raw') & CleanText, TableCell('city') & CleanText)
            obj_original_amount = CleanDecimal(TableCell('original_amount'), default=NotAvailable, replace_dots=True)
            obj_original_currency = FrenchTransaction.Currency(TableCell('original_amount'))
            obj_type = Transaction.TYPE_DEFERRED_CARD
            obj_rdate = obj_bdate = Transaction.Date(TableCell('date'))
            obj_date = obj_vdate = Env('date')
            obj__is_coming = Env('_is_coming')

            obj__gross_amount = CleanDecimal(Env('amount'), replace_dots=True)
            obj_commission = CleanDecimal(Format('-%s', Env('commission')), replace_dots=True, default=NotAvailable)
            obj__to_delete = False

            def obj_amount(self):
                commission = Field('commission')(self)
                gross = Field('_gross_amount')(self)
                if empty(commission):
                    return gross
                return (abs(gross) - abs(commission)).copy_sign(gross)

            def parse(self, el):
                self.env['date'] = Date(
                    Regexp(
                        CleanText('//td[contains(text(), "Total prélevé")]'),
                        r' (\d{2}/\d{2}/\d{4})',
                        default=NotAvailable,
                    ),
                    dayfirst=True,
                    default=NotAvailable,
                )(self)
                if not self.env['date']:
                    try:
                        d = (CleanText('//select[@id="moi"]/option[@selected]')(self)
                             or re.search(r'pour le mois de (.*)', ''.join(w.strip() for w in
                                self.page.doc.xpath('//div[@class="a_blocongfond"]/text()'))).group(1))
                    except AttributeError:
                        d = Regexp(CleanText('//p[has-class("restriction")]'), r'pour le mois de ((?:\w+\s+){2})', flags=re.UNICODE)(self)
                    self.env['date'] = (parse_french_date('%s %s' % ('1', d)) + relativedelta(day=31)).date()
                self.env['_is_coming'] = date.today() < self.env['date']
                amount = CleanText(TableCell('amount'))(self).split('dont frais')
                self.env['amount'] = amount[0]
                self.env['commission'] = amount[1] if len(amount) > 1 else NotAvailable


class ComingPage(OperationsPage, LoggedPage):
    @method
    class get_history(Pagination, Transaction.TransactionsElement):
        head_xpath = '//table[has-class("liste")]//thead//tr/th/text()'
        item_xpath = '//table[has-class("liste")]//tbody/tr'

        col_date = "Date de l'annonce"

        class item(Transaction.TransactionElement):
            obj__is_coming = True


class CardPage(OperationsPage, LoggedPage):
    def select_card(self, card_number):
        for option in self.doc.xpath('//select[@name="Data_SelectedCardItemKey"]/option'):
            card_id = Regexp(CleanText('.', symbols=' '), r'(\d+x+\d+)')(option)
            if card_id != card_number:
                continue
            if Attr('.', 'selected', default=None)(option):
                break

            try:
                # Maybe obsolete
                form = self.get_form(id="I1:fm")
            except FormNotFound:
                form = self.get_form(id='I1:P:F')
            form['_FID_DoChangeCardDetails'] = ""
            form['Data_SelectedCardItemKey'] = Attr('.', 'value')(option)
            return self.browser.open(form.url, data=dict(form)).page
        return self

    @method
    class get_history(Pagination, ListElement):
        class list_cards(ListElement):
            item_xpath = '//table[has-class("liste")]/tbody/tr/td/a'

            class item(ItemElement):
                def __iter__(self):
                    # Here we handle the subtransactions
                    card_link = self.el.get('href')
                    d = re.search(r'cardmonth=(\d+)', self.page.url)
                    if d:
                        year = int(d.group(1)[:4])
                        month = int(d.group(1)[4:])
                    debit_date = date(year, month, 1) + relativedelta(day=31)

                    page = self.page.browser.location(card_link).page

                    for op in page.get_history():
                        op.date = debit_date
                        op.type = FrenchTransaction.TYPE_DEFERRED_CARD
                        op._to_delete = False
                        yield op

        class list_history(Transaction.TransactionsElement):
            head_xpath = '//table[has-class("liste")]//thead/tr/th'
            item_xpath = '//table[has-class("liste")]/tbody/tr'

            col_commerce = 'Commerce'
            col_ville = 'Ville'

            def condition(self):
                return not CleanText('//td[contains(., "Aucun mouvement")]', default=False)(self)

            def parse(self, el):
                label = (
                    CleanText('//span[contains(text(), "Achats")]/following-sibling::span[2]')(el)
                    or CleanText('//*[contains(text(), "Achats")]')(el)
                )
                if not label:
                    return
                try:
                    label = re.findall(r'(\d+ [^ ]+ \d+)', label)[-1]
                except IndexError:
                    return
                # use the trick of relativedelta to get the last day of month.
                self.env['debit_date'] = (parse_french_date(label) + relativedelta(day=31)).date()

            class item(Transaction.TransactionElement):
                condition = lambda self: len(self.el.xpath('./td')) >= 4

                obj_raw = Transaction.Raw(Env('raw'))
                obj_type = Env('type')
                obj_date = Env('debit_date')
                obj_rdate = Transaction.Date(TableCell('date'))
                obj_amount = Env('amount')
                obj_original_amount = Env('original_amount')
                obj_original_currency = Env('original_currency')
                obj__deferred_date = Env('deferred_date')

                def obj_bdate(self):
                    if Field('type')(self) == Transaction.TYPE_DEFERRED_CARD:
                        return Field('rdate')(self)

                def obj__to_delete(self):
                    return bool(CleanText('.//a[contains(text(), "Regroupement")]')(self))

                def parse(self, el):
                    try:
                        self.env['raw'] = Format(
                            '%s %s',
                            CleanText(TableCell('commerce'), children=False),
                            CleanText(TableCell('ville')),
                        )(self)
                    except ColumnNotFound:
                        self.env['raw'] = CleanText(TableCell('commerce'), chilren=False)(self)

                    if CleanText('//span[contains(text(), "Prélevé fin")]', default=None)(self):
                        self.env['type'] = Transaction.TYPE_DEFERRED_CARD
                    else:
                        self.env['type'] = Transaction.TYPE_CARD

                    text_date = (
                        CleanText('//span[contains(text(), "Achats")]/following-sibling::span[2]')(self)
                        or Regexp(CleanText('//*[contains(text(), "Achats")]'), r'(\d+ [^ ]+ \d+)$')(self)
                    )
                    self.env['deferred_date'] = parse_french_date(text_date).date()

                    amount = TableCell('credit')(self)[0]
                    if self.page.browser.is_new_website:
                        if not len(amount.xpath('./div')):
                            amount = TableCell('debit')(self)[0]
                        original_amount = amount.xpath('./div')[1].text if len(amount.xpath('./div')) > 1 else None
                        amount = amount.xpath('./div')[0]
                    else:
                        try:
                            original_amount = amount.xpath('./span')[0].text
                        except IndexError:
                            original_amount = None
                    self.env['amount'] = CleanDecimal(replace_dots=True).filter(amount.text)
                    self.env['original_amount'] = (CleanDecimal(replace_dots=True).filter(original_amount)
                                                   if original_amount is not None else NotAvailable)
                    self.env['original_currency'] = (Account.get_currency(original_amount[1:-1])
                                                     if original_amount is not None else NotAvailable)


class CardPage2(CardPage, HTMLPage, XMLPage):
    def build_doc(self, content):
        if b'<?xml version="1.0"' in content:
            xml = XMLPage.build_doc(self, content)
            html = xml.xpath('//htmlcontent')[0].text.encode(encoding=self.encoding)
            return HTMLPage.build_doc(self, html)

        return super(CardPage2, self).build_doc(content)

    @method
    class get_history(ListElement):
        class list_history(Transaction.TransactionsElement):
            head_xpath = '//table[has-class("liste")]//thead/tr/th'
            item_xpath = '//table[has-class("liste")]/tbody/tr'

            col_commerce = 'Commerce'
            col_ville = 'Ville'

            def condition(self):
                return not CleanText('//td[contains(., "Aucun mouvement")]', default=False)(self) or not CleanText('//td[contains(., "Aucune opération")]', default=False)(self)

            class item(Transaction.TransactionElement):
                def condition(self):
                    # Withdraw transactions are also presents on the checking account
                    return len(self.el.xpath('./td')) >= 4 and not CleanText(TableCell('commerce'))(self).startswith('RETRAIT CB')

                obj_raw = Transaction.Raw(Format("%s %s", CleanText(TableCell('commerce')), CleanText(TableCell('ville'))))
                obj_rdate = obj_bdate = Field('vdate')
                obj_date = Env('date')

                def obj_type(self):
                    if not 'RELEVE' in CleanText('//td[contains(., "Aucun mouvement")]')(self):
                        return Transaction.TYPE_DEFERRED_CARD
                    return Transaction.TYPE_CARD_SUMMARY

                def obj_original_amount(self):
                    m = re.search(r'(([\s-]\d+)+,\d+)', CleanText(TableCell('commerce'))(self))
                    if m and not 'FRAIS' in CleanText(TableCell('commerce'))(self):
                        matched_text = m.group(1)
                        submatch = re.search(r'\d+-(.*)', matched_text)
                        if submatch:
                            matched_text = submatch.group(1)
                        return Decimal(matched_text.replace(',', '.').replace(' ', '')).quantize(Decimal('0.01'))
                    return NotAvailable

                def obj_original_currency(self):
                    m = re.search(r'(\d+,\d+) (\w+)', CleanText(TableCell('commerce'))(self))
                    if Field('original_amount')(self) and m:
                        return m.group(2)

                def obj__is_coming(self):
                    if Field('date')(self) > datetime.date(datetime.today()):
                        return True
                    return False

                # Some payment made on the same organization are regrouped,
                # we have to get the detail for each one later
                def obj__regroup(self):
                    if "Regroupement" in CleanText('./td')(self):
                        return Link('./td/span/a')(self)

    @method
    class get_tr_merged(ListElement):
        class list_history(Transaction.TransactionsElement):
            head_xpath = '//table[@class="liste"]//thead/tr/th'
            item_xpath = '//table[@class="liste"]/tbody/tr'

            col_operation= u'Opération'

            def condition(self):
                return not CleanText('//td[contains(., "Aucun mouvement")]', default=False)(self)

            class item(Transaction.TransactionElement):
                def condition(self):
                    return len(self.el.xpath('./td')) >= 4 and not CleanText(TableCell('operation'))(self).startswith('RETRAIT CB')

                obj_label = CleanText(TableCell('operation'))

                def obj_type(self):
                    if not 'RELEVE' in Field('raw')(self):
                        return Transaction.TYPE_DEFERRED_CARD
                    return Transaction.TYPE_CARD_SUMMARY

                def obj_bdate(self):
                    if Field('type')(self) == Transaction.TYPE_DEFERRED_CARD:
                        return Transaction.Date(TableCell('date'))(self)

    def has_more_operations(self):
        xp = CleanText(self.doc.xpath('//div[@class="ei_blocpaginb"]/a'))(self)
        if xp == 'Suite des opérations':
            return True
        return False

    def has_more_operations_xml(self):
        if self.doc.xpath('//input') and Attr('//input', 'value')(self.doc) == 'Suite des opérations':
            return True
        return False

    @method
    class iter_history_xml(ListElement):
        class list_history(Transaction.TransactionsElement):
            head_xpath = '//thead/tr/th'
            item_xpath = '//tbody/tr'

            col_commerce = 'Commerce'
            col_ville = 'Ville'

            class item(Transaction.TransactionElement):
                def condition(self):
                    # Withdraw transactions are also presents on the checking account
                    return not CleanText(TableCell('commerce'))(self).startswith('RETRAIT CB')

                obj_raw = Transaction.Raw(Format("%s %s", CleanText(TableCell('commerce')), CleanText(TableCell('ville'))))
                obj_rdate = obj_bdate = Field('vdate')
                obj_date = Env('date')

                def obj_type(self):
                    if not 'RELEVE' in CleanText('//td[contains(., "Aucun mouvement")]')(self):
                        return Transaction.TYPE_DEFERRED_CARD
                    return Transaction.TYPE_CARD_SUMMARY

                def obj_original_amount(self):
                    m = re.search(r'(([\s-]\d+)+,\d+)', CleanText(TableCell('commerce'))(self))
                    if m and not 'FRAIS' in CleanText(TableCell('commerce'))(self):
                        matched_text = m.group(1)
                        submatch = re.search(r'\d+-(.*)', matched_text)
                        if submatch:
                            matched_text = submatch.group(1)
                        return Decimal(matched_text.replace(',', '.').replace(' ', '')).quantize(Decimal('0.01'))
                    return NotAvailable

                def obj_original_currency(self):
                    m = re.search(r'(\d+,\d+) (\w+)', CleanText(TableCell('commerce'))(self))
                    if Field('original_amount')(self) and m:
                        return m.group(2)

                def obj__regroup(self):
                    if "Regroupement" in CleanText('./td')(self):
                        return Link('./td/span/a')(self)

                def obj__is_coming(self):
                    if Field('date')(self) > datetime.date(datetime.today()):
                        return True
                    return False

    def get_date(self):
        debit_date = CleanText(self.doc.xpath('//a[@id="C:L4"]'))(self)
        m = re.search(r'(\d{2}/\d{2}/\d{4})', debit_date)
        if m:
            return Date().filter(re.search(r'(\d{2}/\d{2}/\d{4})', debit_date).group(1))
        m = re.search(r'fid=GoMonth&mois=(\d+)', self.browser.url)
        y = re.search(r'annee=(\d+)', self.browser.url)
        if m and y:
            return date(int(y.group(1)), int(m.group(1)), 1) + relativedelta(day=31)
        assert False, 'No transaction date is found'

    def get_amount_summary(self):
        return CleanDecimal('//div[@class="restriction"]/ul[1]/li/span/span/span/b', replace_dots=True)(self.doc) * -1

    def get_links(self):
        links = []

        for link in self.doc.xpath('//div[@class="restriction"]/ul[1]/li'):
            if link.xpath('./span/span/b'):
                break
            tmp_link = Link(link.xpath('./span/span/a'))(self)
            if 'GoMonthPrecedent' in tmp_link:
                secondpage = tmp_link
                continue
            m = re.search(r'fid=GoMonth&mois=(\d+)', tmp_link)
            # To go to the page during the iter_history you need to have the good value from the precedent page
            assert m, "It's not the URL expected"
            m = int(m.group(1))
            m=m+1 if m!= 12 else 1
            url = re.sub(r'(?<=amoiSelectionner%3d)\d+', str(m), tmp_link)
            links.append(url)

        links.reverse()
        # Just for visiting the urls in a chronological way
        m = re.search(r'fid=GoMonth&mois=(\d+)', links[0])
        y = re.search(r'annee=(\d+)', links[0])
        # We need to get a stable coming month instead of "fin du mois"
        if m and y:
            coming_date = date(int(y.group(1)), int(m.group(1)), 1) + relativedelta(months=+1)

            add_first = re.sub(r'(?<=amoiSelectionner%3d)\d+', str(coming_date.month), links[0])
            add_first = re.sub(r'(?<=GoMonth&mois=)\d+', str(coming_date.month), add_first)
            add_first = re.sub(r'(?<=\&annee=)\d+', str(coming_date.year), add_first)
            links.insert(0, add_first)
        m = re.search(r'fid=GoMonth&mois=(\d+)', links[-1]).group(1)
        links.append(re.sub(r'(?<=amoiSelectionner%3d)\d+', str(m), secondpage))

        links2 = []
        page2 = self.browser.open(secondpage).page
        for link in page2.doc.xpath('//div[@class="restriction"]/ul[1]/li'):
            if link.xpath('./span/span/a'):
                tmp_link = Link(link.xpath('./span/span/a'))(self)
                if 'GoMonthSuivant' in tmp_link:
                    break
                m = re.search(r'fid=GoMonth&mois=(\d+)', tmp_link)
                assert m, "It's not the URL expected"
                m = int(m.group(1))
                m=m+1 if m!= 12 else 1
                url = re.sub(r'(?<=amoiSelectionner%3d)\d+', str(m), tmp_link)
                links2.append(url)

        links2.reverse()
        links.extend(links2)
        return links


class LIAccountsPage(LoggedPage, HTMLPage):
    def has_accounts(self):
        # The form only exists if the connection has a life insurance
        return self.doc.xpath('//input[@name="_FID_GoBusinessSpaceLife"]')

    def go_accounts_list(self):
        form = self.get_form(id='C:P14:F', submit='//input[@name="_FID_GoBusinessSpaceLife"]')
        form.submit()

    def has_details(self, account):
        return bool(self.doc.xpath('//input[contains(@value, "%s")]' % account.id))

    def go_account_details(self, account):
        form = self.get_form(id='C:P:F', submit='//input[contains(@value, "%s")]' % account.id)
        form.submit()

    @method
    class iter_li_accounts(TableElement):
        item_xpath = '//table[@class="liste" and contains(.//span/text(), "Liste")]/tbody/tr'
        # The headers are repeated for each account holder, we only take the first row
        head_xpath = '(//table[@class="liste" and contains(.//span/text(), "Liste")])[1]//th'

        col_label = "Titre du contrat"
        col_balance = "Valorisation du contrat"
        col_actions = re.compile(r'Actions')

        class item(ItemElement):
            klass = Account

            def condition(self):
                return TableCell('actions', default=None) is not None

            obj_id = obj_number = Coalesce(
                Base(
                    TableCell('label'),
                    Regexp(CleanText('.//a'), r'Contrat ([^\s]*)', default=NotAvailable)
                ),
                Base(TableCell('label'), Regexp(CleanText('.'), r'Contrat ([^\s]*)'))
            )
            obj_label = Base(TableCell('label'), CleanText('.//em'))

            obj_balance = Base(TableCell('balance'), CleanDecimal.French('.//em', default=NotAvailable))
            obj_currency = Base(TableCell('balance'), Currency('.//em', default=NotAvailable))
            obj__card_links = []
            obj_type = Account.TYPE_LIFE_INSURANCE
            obj__is_inv = True

    @pagination
    @method
    class iter_history(ListElement):
        item_xpath = '//table[has-class("liste")]/tbody/tr'

        def next_page(self):
            next_page = Link('//a[img[@alt="Page suivante"]]', default=None)(self.el)
            if next_page:
                return next_page

        class item(ItemElement):
            klass = FrenchTransaction

            obj_date = obj_rdate = Transaction.Date(CleanText('./td[1]'))
            obj_raw = CleanText('./td[2]')
            obj_amount  = CleanDecimal('./td[4]', replace_dots=True, default=Decimal('0'))
            obj_original_currency = FrenchTransaction.Currency('./td[4]')
            obj_type = Transaction.TYPE_BANK
            obj__is_coming = False

            def obj_commission(self):
                gross_amount = CleanDecimal('./td[3]', replace_dots=True, default=NotAvailable)(self)
                if gross_amount:
                    return gross_amount - Field('amount')(self)
                return NotAvailable

    @method
    class iter_investment(TableElement):
        item_xpath = '//table[has-class("liste") and not (@summary="Avances")]/tbody/tr[count(td)>=7]'
        head_xpath = '//table[has-class("liste") and not (@summary="Avances")]/thead/tr/th'

        col_label = 'Support'
        col_unitprice = re.compile(r'Prix')
        col_vdate = re.compile(r'Date de cotation')
        col_unitvalue = re.compile(r'Valeur de la part')
        col_quantity = 'Nombre de parts'
        col_valuation = 'Valeur atteinte'
        col_srri = re.compile(r'Niveau de risque')
        col_portfolio_share = 'Répartition'
        col_diff_ratio = re.compile(r'Performance UC')

        class item(ItemElement):
            klass = Investment

            obj_label = CleanText(TableCell('label'))
            obj_unitprice = CleanDecimal(TableCell('unitprice', default=NotAvailable),
                                         default=NotAvailable, replace_dots=True)
            obj_vdate = Date(CleanText(TableCell('vdate'), replace=[('-', '')]),
                             default=NotAvailable, dayfirst=True)
            obj_unitvalue = CleanDecimal(TableCell('unitvalue'), default=NotAvailable, replace_dots=True)
            obj_quantity = CleanDecimal(TableCell('quantity'), default=NotAvailable, replace_dots=True)
            obj_valuation = CleanDecimal(TableCell('valuation'), default=Decimal(0), replace_dots=True)

            def obj_srri(self):
                # Columns are not always present
                if TableCell('srri', default=None)(self) is not None:
                    srri = Base(TableCell('srri'), CleanDecimal('.//li[@class="ei_grad_scale_item_selected"]', default=NotAvailable))(self)
                    if srri:
                        return int(srri)
                return NotAvailable

            def obj_portfolio_share(self):
                portfolio_share_percent = CleanDecimal.French(TableCell('portfolio_share', default=None), default=None)(self)
                if portfolio_share_percent is not None:
                    return portfolio_share_percent / 100
                return NotAvailable

            def obj_diff_ratio(self):
                diff_ratio_percent = CleanDecimal.French(TableCell('diff_ratio', default=None), default=None)(self)
                if diff_ratio_percent is not None:
                    return diff_ratio_percent / 100
                return NotAvailable

            def obj_code(self):
                link = Link(TableCell('label')(self)[0].xpath('./a'), default=NotAvailable)(self)
                if not link:
                    return NotAvailable
                return Regexp(pattern=r'isin=([A-Z\d]+)&?', default=NotAvailable).filter(link)


class PorPage(LoggedPage, HTMLPage):
    def get_action_needed_message(self):
        if (
            self.doc.xpath('//form[contains(@action, "MsgCommerciaux")]')
            and self.doc.xpath('//input[contains(@id, "Valider")]')
        ):
            return CleanText('//div[@id="divMessage"]/p[1]')(self.doc)

    def is_message_skippable(self):
        # "Ne plus afficher ce message" checkbox
        return bool(self.doc.xpath('//input[contains(@id, "chxOption")]'))

    def handle_skippable_action_needed(self):
        self.logger.info('Skipping message on PorPage')
        form = self.get_form(id='frmMere')
        form.submit()

    TYPES = {
        "PLAN D'EPARGNE EN ACTIONS": Account.TYPE_PEA,
        'COMPTE DE LIQUIDITE PEA': Account.TYPE_PEA,
        'P.E.A': Account.TYPE_PEA,
        'PEA': Account.TYPE_PEA,
    }

    def get_type(self, label):
        for pattern, actype in self.TYPES.items():
            if label.startswith(pattern):
                return actype
        return Account.TYPE_MARKET

    def find_amount(self, title):
        return None

    def add_por_accounts(self, accounts):
        for por_account in self.iter_por_accounts():
            for account in accounts:
                # we update accounts that were already fetched
                if account.id.startswith(por_account.id) and not account.balance:
                    account._is_inv = por_account._is_inv
                    account.balance = por_account.balance
                    account.currency = por_account.currency
                    account.valuation_diff = por_account.valuation_diff
                    account.valuation_diff_ratio = por_account.valuation_diff_ratio
                    account.type = por_account.type
                    account.url = por_account.url
                    account.number = por_account.number
                    break
            else:
                accounts.append(por_account)

    @method
    class iter_por_accounts(TableElement):
        item_xpath = '//table[@id="tabSYNT"]//tr[td]'
        head_xpath = '//table[@id="tabSYNT"]//th'

        col_raw_label = 'Portefeuille'
        col_balance = re.compile(r'Valorisation en .*')
        col_valuation_diff = re.compile(r'\+/- Value latente en [^%].*')
        col_valuation_diff_ratio = re.compile(r'\+/- Value latente en %.*')

        class item(ItemElement):
            klass = Account

            def condition(self):
                self.env['id'] = CleanText('.//a', replace=[(' ', '')])(self)
                self.env['balance'] = CleanDecimal.French(TableCell('balance'), default=None)(self)
                is_total = 'TOTAL VALO' in CleanText('.')(self)
                is_global_view = Env('id')(self) == 'Vueconsolidée'
                has_empty_balance = Env('balance')(self) is None
                return (
                    not is_total
                    and not is_global_view
                    and not has_empty_balance
                )

            # This values are defined for other types of accounts
            obj__is_inv = True

            # IDs on the old page were differentiated with 5 digits in front of the ID, but not here.
            # We still need to differentiate them so we add ".1" at the end.
            obj_id = Format('%s.1', Env('id'))

            obj_label = Base(TableCell('raw_label'), CleanText('.', children=False))
            obj_number = Base(TableCell('raw_label'), CleanText('./a', replace=[(' ', '')]))

            obj_balance = Env('balance')
            obj_currency = Currency(CleanText('//table[@id="tabSYNT"]/thead//span'), default=NotAvailable)

            obj_valuation_diff = CleanDecimal.French(TableCell('valuation_diff'), default=NotAvailable)

            obj__link_id = Regexp(Link('.//a', default=NotAvailable), r'ddp=([^&]*)', default=NotAvailable)

            def obj_type(self):
                return self.page.get_type(Field('label')(self))

            def obj_valuation_diff_ratio(self):
                valuation_diff_ratio_percent = CleanDecimal.French(TableCell('valuation_diff_ratio'), default=NotAvailable)(self)
                if valuation_diff_ratio_percent:
                    return valuation_diff_ratio_percent / 100
                return NotAvailable

    def fill(self, acc):
        self.send_form(acc)
        ele = self.browser.page.doc.xpath('.//table[has-class("fiche bourse")]')[0]

        balance = CleanText('.//td[contains(@id, "Valorisation")]')(ele)

        # Valorisation will be filled with "NS" string if there isn't information
        if balance == 'NS' and not acc.balance:
            acc.balance = NotAvailable
        else:
            balance = CleanDecimal.French(default=0).filter(balance)
            if acc.balance:
                acc.balance += balance
            else:
                acc.balance = balance
        acc.valuation_diff = CleanDecimal(ele.xpath('.//td[contains(@id, "Variation")]'),
                                          default=Decimal(0), replace_dots=True)(ele)
        if balance:
            acc.currency = Currency('.//td[contains(@id, "Valorisation")]')(ele)
        else:
            # - Table element's textual content also contains dates with slashes.
            # They produce a false match when looking for the currency
            # (Slashes are matched with the Peruvian currency 'S/').
            # - The remaining part of the table textual may contain different
            # balances with their currencies though, so keep this part.
            #
            # Solution: remove the date
            text_content = CleanText('.')(ele)
            date_pattern = r'\d{2}/\d{2}/\d{4}'
            no_date = re.sub(date_pattern, '', text_content)
            acc.currency = Currency().filter(no_date)


    @method
    class iter_investment(TableElement):
        item_xpath = '//table[@id="bwebDynamicTable"]/tbody/tr[not(@id="LigneTableVide")]'
        head_xpath = '//table[@id="bwebDynamicTable"]/thead/tr/th/@abbr'

        col_label = 'Valeur'
        col_unitprice = re.compile('Prix de revient')
        col_unitvalue = 'Cours'
        col_quantity = 'Quantité / Montant nominal'
        col_valuation = 'Valorisation'
        col_diff = '+/- Value latente'

        class item(ItemElement):
            klass = Investment

            def condition(self):
                return not any(not x.isdigit() for x in Attr('.', 'id')(self))

            obj_label = CleanText(TableCell('label'), default=NotAvailable)

            def obj_quantity(self):
                """
                In case of SRD actions, regular actions and SRD quantities are displayed in the same cell,
                we must then add the values in text such as '4 444 + 10000 SRD'
                """

                quantity = CleanText(TableCell('quantity'))(self)
                if '+' in quantity:
                    quantity_list = quantity.split('+')
                    return CleanDecimal.French().filter(quantity_list[0]) + CleanDecimal.French().filter(quantity_list[1])
                else:
                    return CleanDecimal.French().filter(quantity)

            obj_unitprice = CleanDecimal(TableCell('unitprice'), default=Decimal(0), replace_dots=True)
            obj_valuation = CleanDecimal(TableCell('valuation'), default=Decimal(0), replace_dots=True)
            obj_diff = CleanDecimal(TableCell('diff'), default=Decimal(0), replace_dots=True)
            obj_original_currency = Currency(TableCell('unitvalue'))

            def obj_code(self):
                code = Regexp(CleanText('.//td[1]/a/@title'), r'^([^ ]+)')(self)
                if 'masquer' in code:
                    return Regexp(CleanText('./following-sibling::tr[1]//a/@title'), r'^([^ ]+)')(self)
                return code

            def obj_unitvalue(self):
                if Field('original_currency')(self):
                    return NotAvailable

                r = CleanText(TableCell('unitvalue'))(self)
                if r[-1] == '%':
                    return None
                elif r == 'ND':
                    return NotAvailable
                else:
                    return CleanDecimal.French(TableCell('unitvalue'))(self)

            def obj_original_unitvalue(self):
                if Field('original_currency')(self):
                    r = CleanText(TableCell('unitvalue'))(self)
                    if 'ND' in r:
                        return NotAvailable
                    return CleanDecimal.French(TableCell('unitvalue'))(self)

            def obj_vdate(self):
                td = TableCell('unitvalue')(self)[0]
                return Date(Regexp(Attr('./img', 'title', default=''),
                                   r'Cours au : (\d{2}/\d{2}/\d{4})\b', default=None),
                            dayfirst=True, default=NotAvailable)(td)


class IbanPage(LoggedPage, HTMLPage):
    def fill_iban(self, accounts):

        # Old website
        for ele in self.doc.xpath('//table[has-class("liste")]/tr[@class]/td[1]'):
            self.logger.info('On old creditmutuel website')
            for a in accounts:
                if a._is_webid:
                    if a.label in CleanText('.//div[1]')(ele).title():
                        a.iban = CleanText('.//div[5]/em', replace=[(' ', '')])(ele)
                elif self.browser.is_new_website:
                    if a.id in CleanText('.//div[5]/em', replace=[(' ','')])(ele).title():
                        a.iban = CleanText('.//div[5]/em', replace=[(' ', '')])(ele)
                else:
                    if a.id[:-3] in CleanText('.//div[5]/em', replace=[(' ','')])(ele).title():
                        a.iban = CleanText('.//div[5]/em', replace=[(' ', '')])(ele)

        # New website
        for ele in self.doc.xpath('//table[has-class("liste")]//tr[not(@class)]/td[1]'):
            for a in accounts:
                if a.id.split('EUR')[0] in CleanText('.//em[2]', replace=[(' ', '')])(ele):
                    a.iban = CleanText('.//em[2]', replace=[(' ', '')])(ele)


class PorInvestmentsPage(LoggedPage, HTMLPage):
    @method
    class iter_investment(TableElement):
        item_xpath = '//table[@id="tabValorisation"]/tbody/tr[td]'
        head_xpath = '//table[@id="tabValorisation"]/thead//th'

        # Several columns contain two values in the same cell, in two distinct 'div'
        col_label = 'Valeur'  # label & code
        col_quantity = 'Quantité / Montant nominal'
        col_unitvalue = re.compile(r'Cours.*')  # unitvalue & unitprice
        col_valuation = re.compile(r'Valorisation.*')  # valuation & portfolio_share
        col_diff = re.compile(r'\+/- Value latente.*')  # diff & diff_ratio

        class item(ItemElement):
            klass = Investment

            def condition(self):
                # Some invests have 'NB' as their valuation, we filter them out.
                return Base(TableCell('valuation'), CleanDecimal.French('./div[1]', default=None))(self) is not None

            obj_quantity = CleanDecimal.French(TableCell('quantity'))
            obj_label = Base(TableCell('label'), CleanText('./div[1]'))
            obj_code = Base(TableCell('label'), IsinCode(CleanText('./div[2]'), default=NotAvailable))
            obj_code_type = Base(TableCell('label'), IsinType(CleanText('./div[2]'), default=NotAvailable))

            obj_original_currency = Base(TableCell('unitvalue'), Currency('./div[1]', default=NotAvailable))

            def obj_unitvalue(self):
                # The unit value is given in the original currency.
                # All other values are in the account currency.
                if Field('original_currency')(self):
                    return NotAvailable

                # Sometimes we're given a ratio instead of the unit value.
                if '%' in Base(TableCell('unitvalue'), CleanText('./div[1]'))(self):
                    return NotAvailable
                return Base(TableCell('unitvalue'), CleanDecimal.French('./div[1]', default=NotAvailable))(self)

            def obj_original_unitvalue(self):
                if not Field('original_currency')(self):
                    return NotAvailable
                return Base(TableCell('unitvalue'), CleanDecimal.French('./div[1]', default=NotAvailable))(self)

            obj_unitprice = Base(TableCell('unitvalue'), CleanDecimal.French('./div[2]', default=NotAvailable))
            obj_valuation = Base(TableCell('valuation'), CleanDecimal.French('./div[1]'))
            obj_diff = Base(TableCell('diff'), CleanDecimal.French('./div[1]', default=NotAvailable))

            def obj_portfolio_share(self):
                portfolio_share_percent = Base(TableCell('valuation'), CleanDecimal.French('./div[2]', default=None))(self)
                if portfolio_share_percent:
                    return portfolio_share_percent / 100
                return NotAvailable

            def obj_diff_ratio(self):
                diff_ratio_percent = Base(TableCell('diff'), CleanDecimal.French('./div[2]', default=None))(self)
                if diff_ratio_percent:
                    return diff_ratio_percent / 100
                return NotAvailable


class PorHistoryPage(LoggedPage, HTMLPage):
    def submit_date_range_form(self):
        form = self.get_form(id='frmMere')
        form['txtDateSaisie'] = (datetime.today() - relativedelta(years=1)).strftime('%d/%m/%Y')
        form.submit()

    def has_next_page(self):
        return bool(self.doc.xpath('//input[@id="NEXT"]'))

    def submit_next_page_form(self):
        form = self.get_form(id='frmMere')
        form['NEXT.x'] = 0
        form['NEXT.y'] = 0
        form.submit()

    def has_no_transaction(self):
        return bool(self.doc.xpath('//td[@id="bwebTdPasOperation"]'))

    @method
    class iter_history(TableElement):
        item_xpath = '//table[@class="liste bourse"]/tbody/tr[td]'
        head_xpath = '//table[@class="liste bourse"]/thead//th'

        col_date = 'Exécution'
        col_label = 'Opération'
        col_investment_label = 'Valeur'
        col_investment_quantity = re.compile(r'Quantité')
        col_amount = 'Montant net'

        class item(ItemElement):
            klass = Transaction

            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj_label = CleanText(TableCell('label'))
            obj_amount = CleanDecimal.French(TableCell('amount'), default=NotAvailable)
            obj_type = Transaction.TYPE_BANK
            obj__details_link = Base(TableCell('label'), Link('.//a', default=NotAvailable))


class PorHistoryDetailsPage(LoggedPage, HTMLPage):
    @method
    class fill_transaction(ItemElement):
        obj_amount = CleanDecimal.French('//td[@class="tot"]/following-sibling::td[1]')

        def obj_investments(self):
            investment = Investment()
            investment.label = Regexp(CleanText('//td[@id="esdtdLibelleValeur"]'), r'(.*) \(')(self)
            investment.unitprice = CleanDecimal.French('//td[@id="esdtdCrsValeur"]')(self)
            investment.valuation = Field('amount')(self)
            investment.quantity = CleanDecimal.French('//td[@id="esdtdQteValeur"]')(self)
            investment.code = IsinCode(Regexp(CleanText('//td[@id="esdtdLibelleValeur"]'), r'\((.*)\)'), default=NotAvailable)(self)
            investment.code_type = IsinType(Regexp(CleanText('//td[@id="esdtdLibelleValeur"]'), r'\((.*)\)'), default=NotAvailable)(self)
            return [investment]


MARKET_ORDER_DIRECTIONS = {
    'Achat': MarketOrderDirection.BUY,
    'Vente': MarketOrderDirection.SALE,
}

MARKET_ORDER_TYPES = {
    'limit': MarketOrderType.LIMIT,
    'marché': MarketOrderType.MARKET,
    'déclenchement': MarketOrderType.TRIGGER,
}

MARKET_ORDER_PAYMENT_METHODS = {
    'Comptant': MarketOrderPayment.CASH,
}


class PorMarketOrdersPage(PorHistoryPage):
    def has_no_order(self):
        return bool(self.doc.xpath('//td[contains(@id, "PORT_ListeOrdres1_bwebTdPasOrdreEnCours")]'))

    @method
    class iter_market_orders(TableElement):
        item_xpath = '//table[@class="liste bourse"]/tbody/tr[td]'
        head_xpath = '//table[@class="liste bourse"]/thead//th'

        col_date = 'Saisie'
        col_direction = 'Sens'
        col_order_type = 'Modalité'
        col_quantity = re.compile(r'Qté')
        col_label = 'Valeur'
        col_ordervalue = 'Limite-Seuil'
        col_validity_date = re.compile(r'Validité')
        col_state = 'Etat'

        def parse(self, el):
            self.env['date_guesser'] = LinearDateGuesser()

        class item(ItemElement):
            klass = MarketOrder

            def condition(self):
                return 'Remboursement' not in CleanText('.')(self)

            obj_id = Base(TableCell('direction'), Regexp(Link('.//a', default=NotAvailable), r'ref=([^&]+)'))
            obj_direction = Map(
                CleanText(TableCell('direction')),
                MARKET_ORDER_DIRECTIONS,
                MarketOrderDirection.UNKNOWN
            )
            obj_order_type = MapIn(CleanText(TableCell('order_type')), MARKET_ORDER_TYPES, MarketOrderType.UNKNOWN)
            obj_quantity = CleanDecimal.French(TableCell('quantity'))
            obj_label = CleanText(TableCell('label'))

            def obj_ordervalue(self):
                if Field('order_type') in (MarketOrderType.LIMIT, MarketOrderType.TRIGGER):
                    return CleanDecimal.French(Regexp(CleanText(TableCell('ordervalue')), r'[^/]+$'))

            obj_validity_date = Date(CleanText(TableCell('validity_date')), dayfirst=True, default=NotAvailable)

            # The creation date doesn't display the year.
            def obj_date(self):
                validity_date = Field('validity_date')(self)
                match = re.match(r'(?P<day>\d{2})/(?P<month>\d{2}) .*', CleanText(TableCell('date'))(self))
                if match:
                    day = int(match.group('day'))
                    month = int(match.group('month'))
                    # If we have a validity date we can guess the creation year.
                    if validity_date:
                        if validity_date.month > month or validity_date.day >= day:
                            year = validity_date.year
                        else:
                            year = validity_date.year - 1
                        return date(year, month, day)
                    # If we don't have a validity date we use other orders to guess the year.
                    date_guesser = Env('date_guesser')(self)
                    return date_guesser.guess_date(day, month)

            obj_state = CleanText(TableCell('state'))
            obj_code = Base(
                TableCell('label'),
                IsinCode(Regexp(Link('.//a'), r'isin=([^&]+)&'), default=NotAvailable)
            )

            obj__market_order_link = Base(TableCell('direction'), Link('.//a', default=NotAvailable))


class PorMarketOrderDetailsPage(LoggedPage, HTMLPage):
    @method
    class fill_market_order(ItemElement):
        obj_stock_market = Regexp(
            CleanText('//td[contains(@id, "esdtdAchat")]/text()[contains(., "Sur")]'),
            r'Sur (.*)',
            default=NotAvailable
        )
        obj_amount = CleanDecimal.French('//td[contains(@id, "esdtdMntEstimatif")]', default=NotAvailable)
        obj_currency = Coalesce(
            Currency('.//table[@class="liste bourse"]/tbody', default=NotAvailable),
            Currency('//td[contains(@id, "esdtdAchat")]/text()[contains(., "Limite :")]', default=NotAvailable),
            default=NotAvailable,
        )
        obj_payment_method = MapIn(
            CleanText('//td[contains(@id, "esdtdAchat")]/text()[contains(., "Règlement")]'),
            MARKET_ORDER_PAYMENT_METHODS,
            MarketOrderPayment.UNKNOWN
        )


class MyRecipient(ItemElement):
    klass = Recipient

    obj_currency = 'EUR'
    obj_label = CleanText('.//div[@role="presentation"]/em | .//div[not(@id) and not(@role)]')

    def obj_enabled_at(self):
        return datetime.now().replace(microsecond=0)

    def validate(self, el):
        return not el.iban or is_iban_valid(el.iban)


class ListEmitters(ListElement):
    # Emitters page is mostly the same on all the website
    # except for the parent list
    item_xpath = """
        //ul[@id="idDetailListCptDebiter:ul"]//ul/li
        | //ul[@id="idDetailsListCptDebiterVertical:ul"]//ul/li
        | //ul[@id="idDetailsListCptDebiterHorizontal:ul"]//li[@role="radio"]
    """

    def get_bank_info(self):
        bank_info_url = Regexp(
            CleanText('//script[contains(text(), "lien_caisse")]', default=''),
            r'(https://[^"]+)', default=''
        )(self)
        params = parse_qs(urlparse(bank_info_url).query)
        if params.get('guichet'):
            return params['guichet'][0]
        return ''

    class item(ItemElement):
        klass = Emitter

        obj_label = CleanText('./div//em')
        obj_currency = Currency('.//div[@class="_c1 fd _c1"]')
        obj_balance = CleanDecimal.French('.//div[@class="_c1 fd _c1"]')

        def obj_id(self):
            """
            Account IDs have a longer ID than what's shown for the emitters because they contain
            the bank and branch code so we also have to retrieve those.
            """
            bank_info = self.parent.get_bank_info()
            partial_number = CleanText('.//span[@class="_c1 doux _c1"]', replace=[(' ', '')])(self)
            return '%s%s' % (bank_info, partial_number)


class InternalTransferPage(LoggedPage, HTMLPage, AppValidationPage):
    RECIPIENT_STRING = 'data_input_indiceCompteACrediter'
    READY_FOR_TRANSFER_MSG = 'Confirmer un virement entre vos comptes'
    SUMMARY_RECIPIENT_TITLE = 'Compte à créditer'
    IS_PRO_PAGE = False

    def needs_personal_key_card_validation(self):
        return bool(CleanText('//div[contains(@class, "alerte")]/p[contains(text(), "Cette opération nécessite une sécurité supplémentaire")]')(self.doc))

    def can_transfer_pro(self, origin_account):
        for li in self.doc.xpath('//ul[@id="idDetailsListCptDebiterVertical:ul"]//ul/li'):
            if CleanText(li.xpath('.//span[@class="_c1 doux _c1"]'), replace=[(' ', '')])(self) in origin_account:
                return True

    def can_transfer(self, origin_account):
        if self.doc.xpath('//ul[@id="idDetailsListCptDebiterVertical:ul"]') or self.doc.xpath('//ul[@id="idDetailListCptDebiter:ul"]'):
            self.IS_PRO_PAGE = True
            return self.can_transfer_pro(origin_account)

        for li in self.doc.xpath('//ul[@id="idDetailsListCptDebiterHorizontal:ul"]/li'):
            if CleanText(li.xpath('.//span[@class="_c1 doux _c1"]'), replace=[(' ', '')])(self) in origin_account:
                return True

    @method
    class iter_recipients(ListElement):
        def parse(self, el):
            if self.page.IS_PRO_PAGE:
                self.item_xpath = '//ul[@id="idDetailsListCptCrediterVertical:ul"]//ul/li'
            else:
                self.item_xpath = '//ul[@id="idDetailsListCptCrediterHorizontal:ul"]//li[@role="radio"]'

        class item(MyRecipient):
            condition = lambda self: Field('id')(self) not in self.env['origin_account'].id

            obj_bank_name = 'Crédit Mutuel'
            obj_label = CleanText('.//div[@role="presentation"]/em | .//div[not(@id) and not(@role)]')
            obj_id = CleanText('.//span[@class="_c1 doux _c1"]', replace=[(' ', '')])
            obj_category = 'Interne'

            def obj_iban(self):
                l = [a for a in self.page.browser.get_accounts_list()
                     if Field('id')(self) in a.id and empty(a.valuation_diff)]
                assert len(l) == 1
                return l[0].iban

    def get_account_index(self, direction, account):
        for div in self.doc.xpath('//*[has-class("dw_dli_contents")]'):
            inp = div.xpath(".//input")[0]
            if inp.name != direction:
                continue
            acct = div.xpath('.//span[has-class("doux")]')[0].text.replace(" ", "")
            if account.endswith(acct):
                return inp.attrib['value']
        else:
            assert False, 'Transfer origin account %s not found' % account

    def get_from_account_index(self, account):
        return self.get_account_index('data_input_indiceCompteADebiter', account)

    def get_to_account_index(self, account):
        return self.get_account_index(self.RECIPIENT_STRING, account)

    def get_transfer_form(self):
        # internal and external transfer form are differents
        return self.get_form(id='P:F', submit='//input[@type="submit" and contains(@value, "Valider")]')

    def prepare_transfer(self, account, to, amount, reason, exec_date):
        form = self.get_transfer_form()
        form['data_input_indiceCompteADebiter'] = self.get_from_account_index(account.id)
        form[self.RECIPIENT_STRING] = self.get_to_account_index(to.id)
        form['[t:dbt%3adouble;]data_input_montant_value_0_'] = str(amount).replace('.', ',')
        form['[t:dbt%3adate;]data_input_date'] = exec_date.strftime("%d/%m/%Y")
        form['[t:dbt%3astring;x(27)]data_input_libelleCompteDebite'] = reason
        form['[t:dbt%3astring;x(31)]data_input_motifCompteCredite'] = reason
        form['[t:dbt%3astring;x(31)]data_input_motifCompteCredite1'] = reason

        form.submit()

    def get_card_key_validation_link(self):
        return Link('//a[contains(@href, "verif_code")]')(self.doc)

    def check_errors(self):
        # look for known errors
        content = self.text
        messages = [
            'Le montant du virement doit être positif, veuillez le modifier',
            'Le solde de votre compte est insuffisant',
            'Nom prénom du bénéficiaire différent du titulaire. Utilisez un compte courant',
            "Pour effectuer cette opération, vous devez passer par l’intermédiaire d’un compte courant",
            'Débit interdit sur ce compte',
            "L'intitulé du virement ne peut contenir le ou les caractères suivants",
            'La date ne peut être inférieure à la date du jour. Veuillez la corriger',
            'Dépassement du montant',
            'Le guichet précisé dans le RIB du destinataire est inconnu',
            'Opération non conforme',
            'Virement interdit',
            'Montant maximum autorisé',
            'Votre ordre peut être traité au plus tard le',
        ]

        for message in messages:
            if message in content:
                full_message = CleanText('//div[@class="blocmsg err"]/p')(self.doc)
                if full_message:
                    # get full error message
                    message = full_message
                raise TransferBankError(message=message)

    def check_success(self):
        # look for the known "all right" message
        assert self.doc.xpath('//span[contains(text(), $msg)]', msg=self.READY_FOR_TRANSFER_MSG), \
               'The expected transfer message "%s" was not found.' % self.READY_FOR_TRANSFER_MSG

    def check_data_consistency(self, account_id, recipient_id, amount, reason):
        assert account_id in CleanText('//div[div[p[contains(text(), "Compte à débiter")]]]',
                                       replace=[(' ', '')])(self.doc)
        assert recipient_id in CleanText('//div[div[p[contains(text(), "%s")]]]' % self.SUMMARY_RECIPIENT_TITLE,
                                         replace=[(' ', '')])(self.doc)

        exec_date = Date(Regexp(CleanText('//table[@summary]/tbody/tr[th[contains(text(), "Date")]]/td'),
                                r'(\d{2}/\d{2}/\d{4})'), dayfirst=True)(self.doc)
        r_amount = CleanDecimal('//table[@summary]/tbody/tr[th[contains(text(), "Montant")]]/td',
                                replace_dots=True)(self.doc)
        assert r_amount == Decimal(amount)
        currency = FrenchTransaction.Currency('//table[@summary]/tbody/tr[th[contains(text(), "Montant")]]/td')(self.doc)

        if reason is not None:
            creditor_label = CleanText('.').filter(reason.upper()[:22])
            debitor_label = CleanText('//table[@summary]/tbody/tr[th[contains(text(), "Intitulé pour le compte à débiter")]]/td')(self.doc)
            assert creditor_label in debitor_label, 'Difference in label between the debitor and the creditor'

        return exec_date, r_amount, currency

    def get_transfer_webid(self):
        parsed = urlparse(self.url)
        return parse_qs(parsed.query)['_saguid'][0]

    def handle_response(self, account, recipient, amount, reason, exec_date):
        self.check_errors()
        self.check_success()

        exec_date, r_amount, currency = self.check_data_consistency(account.id, recipient.id, amount, reason)

        transfer = Transfer()
        transfer.currency = currency
        transfer.amount = r_amount
        transfer.account_iban = account.iban
        transfer.recipient_iban = recipient.iban
        transfer.account_id = account.id
        transfer.recipient_id = recipient.id
        transfer.exec_date = exec_date
        transfer.label = reason

        transfer.account_label = account.label
        transfer.recipient_label = recipient.label
        transfer.account_balance = account.balance
        transfer.id = self.get_transfer_webid()

        return transfer

    def create_transfer(self, transfer):
        self.check_errors()
        # look for the known "everything went well" message
        content = self.text
        transfer_ok_message = ['Votre virement a &#233;t&#233; ex&#233;cut&#233;',
                               'Ce virement a &#233;t&#233; enregistr&#233; ce jour',
                               'Ce virement a été enregistré ce jour']
        assert any(msg for msg in transfer_ok_message if msg in content), \
            'The expected transfer message "%s" was not found.' % transfer_ok_message

        exec_date, r_amount, currency = self.check_data_consistency(transfer.account_id, transfer.recipient_id, transfer.amount, transfer.label)

        state = CleanText('//table[@summary]/tbody/tr[th[contains(text(), "Etat")]]/td')(self.doc)
        valid_states = ('Exécuté', 'Soumis', 'A exécuter')

        # tell user that transfer was done even though it wasn't done is better
        # than tell users that transfer state is bug even though it was done
        for valid_state in valid_states:
            if valid_state in state:
                break
        else:
            assert False, 'Transfer state is %r' % state

        assert transfer.amount == r_amount
        assert transfer.exec_date == exec_date
        assert transfer.currency == currency

        return transfer

    @method
    class iter_emitters(ListEmitters):
        pass


class ExternalTransferPage(InternalTransferPage):
    RECIPIENT_STRING = 'data_input_indiceBeneficiaire'
    READY_FOR_TRANSFER_MSG = 'Confirmer un virement vers un bénéficiaire enregistré'
    SUMMARY_RECIPIENT_TITLE = 'Bénéficiaire à créditer'

    def can_transfer_pro(self, origin_account):
        for li in self.doc.xpath('//ul[@id="idDetailListCptDebiter:ul"]//ul/li'):
            if CleanText(li.xpath('.//span[@class="_c1 doux _c1"]'), replace=[(' ', '')])(self) in origin_account:
                return True

    def has_transfer_categories(self):
        select_elem = self.doc.xpath('//select[@name="data_input_indiceMarqueurListe"]')
        if select_elem:
            assert len(select_elem) == 1
            return True

    def iter_categories(self):
        for option in self.doc.xpath('//select[@name="data_input_indiceMarqueurListe"]/option'):
            # This is the default selector
            if option.attrib['value'] == '9999':
                continue
            yield {'name': CleanText('.')(option), 'index': option.attrib['value']}

    def go_on_category(self, category_index):
        form = self.get_form(id='P2:F', submit='//input[@type="submit" and @value="Nom"]')
        form['data_input_indiceMarqueurListe'] = category_index
        form.submit()

    @method
    class iter_recipients(ListElement):
        def parse(self, el):
            if self.page.IS_PRO_PAGE:
                self.item_xpath = '//ul[@id="ben.idBen:ul"]/li'
            else:
                self.item_xpath = '//ul[@id="idDetailListCptCrediterHorizontal:ul"]/li'

        class item(MyRecipient):
            def condition(self):
                return Field('id')(self) not in self.env['origin_account']._external_recipients

            obj_bank_name = CleanText('(.//span[@class="_c1 doux _c1"])[2]', default=NotAvailable)
            obj_label = CleanText('./div//em')

            def obj_category(self):
                return self.env['category'] if 'category' in self.env else 'Externe'

            def obj_id(self):
                if self.page.IS_PRO_PAGE:
                    return CleanText('(.//span[@class="_c1 doux _c1"])[1]', replace=[(' ', '')])(self.el)
                else:
                    return CleanText('.//span[@class="_c1 doux _c1"]', replace=[(' ', '')])(self.el)

            def obj_iban(self):
                return Field('id')(self)

            def parse(self, el):
                self.env['origin_account']._external_recipients.add(Field('id')(self))

    def get_transfer_form(self):
        # transfer form id change from "P1:F" to "P2:F" and from "P2:F" to "P3:F"
        # search for other info to get transfer form
        transfer_form_xpath = '//form[contains(@action, "fr/banque/virements/vplw") and @method="post"]'
        transfer_form_submit_xpath = '//input[@type="submit" and contains(@value, "Valider")]'
        return self.get_form(xpath=transfer_form_xpath, submit=transfer_form_submit_xpath)

    @method
    class iter_emitters(ListEmitters):
        pass


class VerifCodePage(LoggedPage, HTMLPage):
    HASHES = {
        ('b1b472cb6e6adc28bfdcc4bc86661fa7', 'f8d9330f322575cb3d5853c347c4ed16'): 'A1',
        '72b11c4c4991a6ec37126a8892f9e398': 'A2',
        'dce4a0228485a23f490ebbdd7ec96bff': 'A3',
        'b09099c0cccfa5843793e29cc6b50c2e': 'A4',
        '83fdc778b984cc7df4c54c74b3e06118': 'A5',
        '70e1292e81678f3dd4463dc78ac20c23': 'A6',
        '6a3c5cecde55a71af5ad52f3c3218bd8': 'A7',
        'e94d438f1301e7ba7b69061b09766d2d': 'A8',
        'd0eb3289d399cc070963cdbe8ed74482': 'B1',
        'c66bbade362a73b5a1304d15ba7cec3f': 'B2',
        '698e0ed53572c112bdcd4e02b90c0f76': 'B3',
        '453023b486e4baddc5c866fd3a0dae6a': 'B4',
        '78cab490f003eaa6c9260a08daee3c48': 'B5',
        'a6cf0fed8511f655421c9d1d6c1dfae9': 'B6',
        '79873f1e9af1833b9691dcd9c97096ac': 'B7',
        '6f76286584707a1e6a0d8e3d421f7b0d': 'B8',
        '57f365a252248e9376003329cd798fd3': 'C1',
        'd052368c3aba2c4296669b25dc3f5b83': 'C2',
        '423612655316cdb050378004b2bc5d2e': 'C3',
        'd679310f45034c095afcaa88a5422256': 'C4',
        'd739655c364a3b489260be7b42a13252': 'C5',
        '7c1e33515a42bfd819a6004f78b09615': 'C6',
        '55ffe065456d33e70152ad860154d190': 'C7',
        '13a927f61873ba6f2615fb529608629f': 'C8',
        'e48146297f68ce172b9d4092827fbd2c': 'D1',
        '92ee176c2ee21821066747ca22ab42f0': 'D2',
        'b405d1912ba172052c198b14b50db18f': 'D3',
        '6a65689653e2465fc50e8765b8d5f89b': 'D4',
        'de0f615ea01463a764e5031a696160a2': 'D5',
        'b90f7ee198f0384480d79f7ebc8e8d3c': 'D6',
        '844def4fead85f22e280a5b379b69492': 'D7',
        '7485085e2dda01d90a190371509518d5': 'D8',
        'd2142b7028ee0e67f03379468243ab09': 'E1',
        'c42126f7c01365992c2a99d6164c6599': 'E2',
        '978172427932c2a2a867baa25eb68ee0': 'E3',
        '837c374cba2c11cfea800aaff06ca0b1': 'E4',
        '041deaaff4b0d312f99afd5d9256af6c': 'E5',
        'a3d2eea803f71200b851146d6f57998b': 'E6',
        '9cd913b53b6cd028bd609b8546af9b0d': 'E7',
        '17308564239363735a6a9f34021d26a9': 'E8',
        '89b913bc935a3788bf4fe6b35778a372': 'F1',
        '7651835218b5a7538b5b9d20546d014b': 'F2',
        'f32bcdac80720bf39927dde41a8a21b8': 'F3',
        '4ed222ecfd6676fcb6a4908ce915e63d': 'F4',
        '4151f3c6531cde9bc6a1c44e89d9e47a': 'F5',
        '6a2987e43cccc6a265c37aa73bb18703': 'F6',
        '67f777297fec2040638378fae4113aa5': 'F7',
        '50c2c36fbb49e64365f43068ee76c521': 'F8',
        '8c38cd983eac6a02080c880e9c5c5a42': 'G1',
        '4eba3b877f4e99fadb9369bfea6bc100': 'G2',
        '4a3f409303806e53f8d5397a24fa0966': 'G3',
        '88a7ec3d1377f913969c05859489020b': 'G4',
        '4feda60f9dce97a400b3a6e07c8ad3f1': 'G5',
        '0247c3ab8786018e4b324b05991a137c': 'G6',
        'c3c2eac333cc3f8ff6b7d2814ad51943': 'G7',
        '395881853e2d2fe7ed317c0b82227c8c': 'G8',
        '213cab37d52ebcddd3950f2132fdeafd': 'H1',
        'a826e4b3f2bfc9e07882a55929523a21': 'H2',
        'cb4c92a05ef2c621b49b3b12bdc1676e': 'H3',
        '641883bd5878f512b6bcd60c53872749': 'H4',
        '9e5541bd54865ba57514466881b9db41': 'H5',
        '03cc8d41cdf5e3d8d7e3f11b25f1cd5c': 'H6',
        '203ec0695ec93bfd947c33b41802562b': 'H7',
        'cbd1e9d2276ecc9cd7e6cae9b0127d58': 'H8',
    }

    def on_load(self):
        actions_needed = (
            CleanText('//p[contains(text(), "Carte de CLÉS PERSONNELLES révoquée")]')(self.doc),
            CleanText('//p[contains(text(), "votre carte") and contains(text(), "a été révoquée")]')(self.doc),
        )
        for action_needed in actions_needed:
            if action_needed:
                if 'En appuyant sur "Retour"' in action_needed:
                    # The second part contains the message "En appuyant sur
                    # "Retour" vous allez être redirigé vers l'application appelante"
                    # which might be misleading.
                    action_needed = action_needed.split('.')[0]
                raise ActionNeeded(action_needed)

    def get_key_case(self, _hash):
        for h, v in self.HASHES.items():
            if h == _hash or _hash in h:
                return v

    def get_personal_keys_error(self):
        error = CleanText('//div[contains(@class, "alerte")]/p')(self.doc)
        if error:
            if 'Vous ne possédez actuellement aucune Carte de Clés Personnelles active' in error:
                return error
            raise AssertionError('Unhandled personal key card error : "%s"' % error)

    def get_question(self):
        question = Regexp(CleanText('//div/p[input]'), r'(Veuillez .*):')(self.doc)
        if CleanText('//div[p[input] and p[img]]')(self.doc):
            # The case name is an image
            img_base64 = Attr('//div[p[input] and p[img]]/p/img', 'src')(self.doc)
            img_base64 = img_base64.replace('data:image/png;base64,', '')

            img_md5 = md5(img_base64.encode('ascii')).hexdigest()
            key_case = self.get_key_case(img_md5)
            assert key_case, "Unhandled image hash : '%s'" % img_md5

            question = question.replace('case', 'case %s' % key_case)
        return question

    def get_personal_key_card_code_form(self):
        form = self.get_form('//form[contains(@action, "verif_code")]')
        key_form = dict(form.items())
        key_form['url'] = form.url
        return key_form

    def get_error(self):
        errors = (
            CleanText('//p[contains(text(), "Clé invalide !")]')(self.doc),
            CleanText('//p[contains(text(), "Vous n\'avez pas saisi de clé")]')(self.doc),
            CleanText('//p[contains(text(), "saisie est incorrecte")]')(self.doc),
            CleanText('//p[contains(text(), "Vous n\'êtes pas inscrit") and a[text()="service d\'identification renforcée"]]')(self.doc),
            CleanText('//p[contains(text(), "L\'information KeyInput est incorrecte")]')(self.doc),
        )
        for error in errors:
            if error:
                if "L'information KeyInput est incorrecte" in error:
                    error = 'Votre saisie est incorrecte.'
                return error

        error_msg = CleanText('//div[@class="blocmsg info"]/p')(self.doc)
        # the card was not activated yet
        if 'veuillez activer votre carte' in error_msg:
            return error_msg


class RecipientsListPage(LoggedPage, HTMLPage):
    def on_load(self):
        txt = CleanText('//em[contains(text(), "Protection de vos opérations en ligne")]')(self.doc)
        if txt:
            self.browser.location(Link('//div[@class="blocboutons"]//a')(self.doc))

        error = CleanText('//div[@class="blocmsg err"]/p')(self.doc)
        if error and not self.bic_needed():
            # don't reload state if it fails because it's not supported by the website
            self.browser.need_clear_storage = True
            raise AddRecipientBankError(message=error)

    def has_list(self):
        return any((
            CleanText('//th[contains(text(), "Listes pour virements ordinaires")]')(self.doc),
            CleanText('//th[contains(text(), "Listes pour virements spéciaux")]')(self.doc),
        ))

    def get_recipients_list(self):
        return [CleanText('.')(a) for a in self.doc.xpath('//a[@title="Afficher le bénéficiaires de la liste"]')]

    def go_list(self, category):
        form = self.get_form(id='P1:F', submit='//input[@value="%s"]' % category)
        del form['_FID_DoAjoutListe']
        form.submit()

    def go_to_add(self):
        form = self.get_form(id='P1:F', submit='//input[@value="Ajouter"]')
        form.submit()

    def get_add_recipient_form(self, recipient):
        form = self.get_form(id='P:F')
        del form['_FID_GoI%5fRechercheBIC']
        form['[t:dbt%3astring;x(70)]data_input_nom'] = recipient.label
        form['[t:dbt%3astring;x(34)]data_input_IBANBBAN'] = recipient.iban
        form['_FID_DoValidate'] = ''

        # Needed because it requires that \xe9 is encoded %E9 instead of %C3%A9
        try:
            del form['data_pilotageAffichage_habilitéSaisieInternationale']
        except KeyError:
            pass
        else:
            form[b'data_pilotageAffichage_habilit\xe9SaisieInternationale'] = ''
        return form

    def add_recipient(self, recipient):
        form = self.get_add_recipient_form(recipient)
        form.submit()

    def bic_needed(self):
        error = CleanText('//div[@class="blocmsg err"]/p')(self.doc)
        if error == 'Le BIC est obligatoire pour ce type de compte':
            return True

    def set_browser_form(self, form):
        self.browser.recipient_form = dict((k, v) for k, v in form.items() if v)
        self.browser.recipient_form['url'] = form.url

    def ask_bic(self, recipient):
        form = self.get_add_recipient_form(recipient)
        self.set_browser_form(form)
        raise AddRecipientStep(recipient, Value('Bic', label='Veuillez renseigner le BIC'))

    def ask_auth_validation(self, recipient):
        form = self.get_form(id='P:F')
        self.set_browser_form(form)

        app_validation = CleanText('//h2[contains(./strong/text(), "Démarrez votre application mobile")]')(self.doc)
        if app_validation:
            self.browser.recipient_form['transactionId'] = Regexp(CleanText('//script[contains(text(), "transactionId")]'), r"transactionId: '(.{49})', get")(self.doc)
            raise AppValidation(
                resource=recipient,
                message=app_validation
            )

        sms_validation = CleanText('//span[contains(text(), "Pour confirmer votre opération, indiquez votre ")]')(self.doc)
        if sms_validation:
            raise AddRecipientStep(recipient, Value('code', label=sms_validation))

        # don't reload state if it fails because it's not supported by the website
        self.browser.need_clear_storage = True
        assert False, 'Was expecting a page where sms code or app validation is asked'

class RevolvingLoansList(LoggedPage, HTMLPage):
    @method
    class iter_accounts(ListElement):
        item_xpath = '//tbody/tr'
        flush_at_end = True

        class item_account(ItemElement):
            klass = Loan

            def condition(self):
                return len(self.el.xpath('./td')) >= 5

            obj_label = CleanText('.//td[2]')
            obj_total_amount = MyDecimal('.//td[3]')
            obj_currency = FrenchTransaction.Currency(CleanText('.//td[3]'))
            obj_type = Account.TYPE_REVOLVING_CREDIT
            obj__is_inv = False
            obj__link_id = None
            obj_number = Field('id')

            def obj_id(self):
                if self.el.xpath('.//a') and not 'notes' in Attr('.//a','href')(self):
                    return Regexp(Attr('.//a','href'), r'(\d{16})\d{2}$')(self)
                return Regexp(Field('label'), r'(\d+ \d+)')(self).replace(' ', '')

            def load_details(self):
                self.async_load = False
                if self.el.xpath('.//a') and not 'notes' in Attr('.//a','href')(self):
                    self.async_load = True
                    return self.browser.async_open(Attr('.//a','href')(self))
                return NotAvailable

            def obj_balance(self):
                if self.async_load:
                    async_page = Async('details').loaded_page(self)
                    return MyDecimal(
                        Format('-%s',CleanText('//main[@id="ei_tpl_content"]/div/div[2]/table//tr[2]/td[1]')))(async_page)
                return -Field('used_amount')(self)

            def obj_available_amount(self):
                if self.async_load:
                    async_page = Async('details').loaded_page(self)
                    return MyDecimal('//main[@id="ei_tpl_content"]/div/div[2]/table//tr[3]/td[1]')(async_page)
                return NotAvailable

            def obj_used_amount(self):
                if not self.async_load:
                    return MyDecimal(Regexp(CleanText('.//td[5]'), r'([\s\d-]+,\d+)'))(self)

            def obj_next_payment_date(self):
                if not self.async_load:
                    return Date(Regexp(CleanText('.//td[4]'), r'(\d{2}/\d{2}/\d{2})'))(self)

            def obj_next_payment_amount(self):
                if not self.async_load:
                    return MyDecimal(Regexp(CleanText('.//td[4]'), r'([\s\d-]+,\d+)'))(self)

            def obj_rate(self):
                if not self.async_load:
                    return MyDecimal(Regexp(CleanText('.//td[2]'), r'.* (\d*,\d*)%', default=NotAvailable))(self)


class ErrorPage(HTMLPage):
    def on_load(self):
        error = CleanText('//td[@class="ALERTE"]')(self.doc)
        if error:
            raise BrowserUnavailable(error)

class RevolvingLoanDetails(LoggedPage, HTMLPage):
    pass


class SubscriptionPage(LoggedPage, HTMLPage):
    def submit_form(self, subscriber):
        form = self.get_form(id='frmDoc')
        form['SelTiers'] = subscriber
        form.submit()

    def get_subscriptions(self, subscription_list, subscriber=None):
        for account in self.doc.xpath('//table[@class="liste"]//tr//td[contains(text(), "Compte")]'):
            sub = Subscription()
            sub.id = Regexp(CleanText('.', replace=[('.', ''), (' ', '')]), r'(\d+)')(account)

            if find_object(subscription_list, id=sub.id):
                continue

            sub.label = CleanText('.')(account)

            if subscriber != None:
                sub.subscriber = CleanText('.')(subscriber)
            else:
                sub.subscriber = CleanText('//span[@id="NomTiers"]')(self.doc)

            subscription_list.append(sub)
            yield sub

    def iter_subscriptions(self):
        subscription_list = []

        options = self.doc.xpath('//select[@id="SelTiers"]/option')
        if options:
            for opt in options:
                subscriber = self.doc.xpath('//select[@id="SelTiers"]/option[contains(text(), $subscriber)]', subscriber=CleanText('.')(opt))[0]
                self.submit_form(Attr('.', 'value')(subscriber))
                for sub in self.get_subscriptions(subscription_list, subscriber):
                    yield sub
        else:
            for sub in self.get_subscriptions(subscription_list):
                yield sub

    @method
    class iter_documents(TableElement):
        item_xpath = '//table[caption[contains(text(), "Extraits de comptes")]]//tr[td]'
        head_xpath = '//table[@class="liste"]//th'

        col_date = 'Date'
        col_label = 'Information complémentaire'
        col_url = 'Nature du document'

        class item(ItemElement):
            def condition(self):
                # For some documents like "Synthèse ISF", the label column is empty.
                # Consequently we can't associate the document to an account: we skip it.
                return CleanText(TableCell('label'))(self) and Env('sub_id')(self) == Regexp(CleanText(TableCell('label'), replace=[('.', ''), (' ', '')]), r'(\d+)')(self)

            klass = Document

            # Some documents may have the same date, name and label; only parts of the PDF href may change,
            # so we must pick a unique ID including the href to avoid document duplicates:
            obj_id = Format('%s_%s_%s', Env('sub_id'), CleanText(TableCell('date'), replace=[('/', '')]),
                            Regexp(Field('url'), r'NOM=(.*)&RFL='))
            obj_label = Format('%s %s', CleanText(TableCell('url')), CleanText(TableCell('date')))
            obj_date = Date(CleanText(TableCell('date')), dayfirst=True)
            obj_format = 'pdf'
            obj_type = DocumentTypes.OTHER

            def obj_url(self):
                return urljoin(self.page.url, '/fr/banque/%s' % Link('./a')(TableCell('url')(self)[0]))

    def next_page(self):
        form = self.get_form(id='frmDoc')
        form['__EVENTTARGET'] = ''
        form['__EVENTARGUMENT'] = ''
        form['__LASTFOCUS'] = ''
        form['SelIndex1'] = ''
        form['NEXT.x'] = '7'
        form['NEXT.y'] = '8'
        form.submit()

    def is_last_page(self):
        if self.doc.xpath('//td[has-class("ERREUR")]'):
            return True
        if re.search(r'(\d\/\d)', CleanText('//div[has-class("blocpaginb")]', symbols=' ')(self.doc)):
            return True
        return False


class NewCardsListPage(LoggedPage, HTMLPage):
    @pagination
    @method
    class iter_accounts(ListElement):
        item_xpath = '//li[@class="item"]'
        def next_page(self):
            other_cards = self.el.xpath('//span/a[contains(text(), "Autres cartes")]')
            if other_cards:
                self.page.browser.two_cards_page = True
                return Link(other_cards)(self)

        class item(ItemElement):
            klass = Account

            def condition(self):
                # Numerous cards are not deferred card, we keep the card only if there is a coming
                return 'Dépenses' in CleanText('.//tr[1]/td/a[contains(@id,"C:more-card")]')(self) and (CleanText('.//div[1]/p')(self) == 'Active' or Field('coming')(self) != 0)

            obj_balance = 0
            obj_type = Account.TYPE_CARD
            obj_number = Field('id')
            obj__new_space = True
            obj__is_inv = False

            def obj__secondpage(self):
                # Necessary to reach the good history page
                return ('DistributedCards' in self.page.url)

            def obj_currency(self):
                curr = CleanText('.//tbody/tr[1]/td/span')(self)
                return re.search(r' ([a-zA-Z]+)', curr).group(1)

            def obj_id(self):
                m = re.search(r'\d{4} \d{2}XX XXXX \d{4}', CleanText('.//span')(self))
                assert m, 'Id card is not present'
                return m.group(0).replace(' ', '').replace('X', 'x')

            def obj_label(self):
                label = CleanText('.//span/span')(self)
                return re.search(r'(.*) - ', label).group(1)

            def obj_coming(self):
                coming = 0
                coming_xpath = self.el.xpath('.//tbody/tr/td/span')
                if len(coming_xpath) >= 1:
                    for i in (1, 2):
                        href = Link('.//tr[%s]/td/a[contains(@id,"C:more-card")]' %(i))(self)
                        m = re.search(r'selectedMonthly=(.*)', href).group(1)
                        if date(int(m[-4:]), int(m[:-4]), 1) + relativedelta(day=31) > date.today():
                            coming += CleanDecimal(coming_xpath[i-1], replace_dots=True)(self)
                else:
                    # Sometimes only one month is available
                    href = Link('//tr/td/a[contains(@id,"C:more-card")]')(self)
                    m = re.search(r'selectedMonthly=(.*)', href).group(1)
                    if date(int(m[-4:]), int(m[:-4]), 1) + relativedelta(day=31) > date.today():
                        coming += CleanDecimal(coming_xpath[0], replace_dots=True)(self)
                return coming

            def obj__link_id(self):
                return Link('.//a[contains(@id,"C:more-card")]')(self)

            def obj__parent_id(self):
                return re.search(r'\d+', CleanText('./div/div/div/p', replace=[(' ', '')])(self)).group(0)[-16:]

            def parse(self, el):
                # We have to reach the good page with the information of the type of card
                history_page = self.page.browser.open(Field('_link_id')(self)).page
                card_type_page = Link('//div/ul/li/a[contains(text(), "Fonctions")]', default=NotAvailable)(history_page.doc)
                if card_type_page:
                    doc = self.page.browser.open(card_type_page).page.doc
                    card_type_line = doc.xpath('//tbody/tr[th[contains(text(), "Débit des paiements")]]') or doc.xpath(u'//div[div/div/p[contains(text(), "Débit des paiements")]]')
                    if card_type_line:
                        if 'Différé' not in CleanText('.//td')(card_type_line[0]):
                            raise SkipItem()
                    elif doc.xpath('//div/p[contains(text(), "Vous n\'avez pas l\'autorisation")]'):
                        self.logger.warning("The user can't reach this page")
                    elif doc.xpath('//td[contains(text(), "Problème technique")]'):
                        raise BrowserUnavailable(CleanText(doc.xpath('//td[contains(text(), "Problème technique")]'))(self))
                    else:
                        assert False, 'xpath for card type information could have changed'
                elif not CleanText('//ul//a[contains(@title, "Consulter le différé")]')(history_page.doc):
                    # If the card is not active the "Fonction" button is absent.
                    # However we can check "Consulter le différé" button is present
                    raise SkipItem()

    def get_unavailable_cards(self):
        cards = []
        for card in self.doc.xpath('//li[@class="item"]'):
            if CleanText(card.xpath('.//div[1]/p'))(self) != 'Active':
                m = re.search(r'\d{4} \d{2}XX XXXX \d{4}', CleanText(card.xpath('.//span'))(self))
                if m:
                    cards.append(m.group(0).replace(' ', '').replace('X', 'x'))
        return cards

    def get_second_page_link(self):
        other_cards = self.doc.xpath('//span/a[contains(text(), "Autres cartes")]')
        if other_cards:
            return Link(other_cards)(self)


class ConditionsPage(LoggedPage, HTMLPage):
    pass


class OutagePage(HTMLPage):
    pass
