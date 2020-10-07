# -*- coding: utf-8 -*-

# Copyright(C) 2020      olivm38
#
# This file is part of weboob.
#
# weboob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# weboob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with weboob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import unicode_literals

import re

from weboob.browser.filters.standard import CleanText
from weboob.browser.pages import PartialHTMLPage
from weboob.tools.capabilities.bank.transactions import FrenchTransaction
from weboob.tools.date import parse_french_date


class BankTransaction(FrenchTransaction):
    '''PATTERNS = [
        (re.compile(r'^RET(RAIT) DAB (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'), FrenchTransaction.TYPE_WITHDRAWAL),
        (re.compile(r'^(CARTE|CB ETRANGER|CB) (?P<dd>\d{2})/(?P<mm>\d{2}) (?P<text>.*)'), FrenchTransaction.TYPE_CARD),
        (re.compile(r'^(?P<category>VIR(EMEN)?T? (SEPA)?(RECU|FAVEUR)?)( /FRM)?(?P<text>.*)'), FrenchTransaction.TYPE_TRANSFER),
        (re.compile(r'^PRLV (?P<text>.*)( \d+)?$'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^(CHQ|CHEQUE) .*$'), FrenchTransaction.TYPE_CHECK),
        (re.compile(r'^(AGIOS /|FRAIS) (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(CONVENTION \d+ |F )?COTIS(ATION)? (?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^(F|R)-(?P<text>.*)'), FrenchTransaction.TYPE_BANK),
        (re.compile(r'^REMISE (?P<text>.*)'), FrenchTransaction.TYPE_DEPOSIT),
        (re.compile(r'^(?P<text>.*)( \d+)? QUITTANCE .*'), FrenchTransaction.TYPE_ORDER),
        (re.compile(r'^.* LE (?P<dd>\d{2})/(?P<mm>\d{2})/(?P<yy>\d{2})$'), FrenchTransaction.TYPE_UNKNOWN),
        (re.compile(r'^ACHATS (CARTE|CB)'), FrenchTransaction.TYPE_CARD_SUMMARY),
        (re.compile(r'^ANNUL (?P<text>.*)'), FrenchTransaction.TYPE_PAYBACK)
    ]'''

class TransactionsPage(PartialHTMLPage):
    def get_transactions(self):
        dates = self.doc.xpath("//div[@id='table_transacs']//div[@class='transaction-date']")
        for i in range(1, len(dates) + 1):
            date = parse_french_date(CleanText("//div[@id='table_transacs']//div[@class='transaction-date'][{0}]".format(i))(self.doc))
            transactions = self.doc.xpath("//div[@id='table_transacs']//table[contains(@class,'transaction-list')][{0}]//tr".format(i))
            for trans in transactions:
                t = BankTransaction()
                raw = CleanText('.//td[@class="transaction-details"]')(trans)
                credit = CleanText('.//td[contains(@class, "transaction-amount")]')(trans)

                t.parse(date, re.sub(r'[ ]+', ' ', raw), vdate=date)
                t.set_amount(re.sub(r'[.]', ',', credit))
                yield t
