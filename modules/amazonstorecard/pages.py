# Copyright(C) 2014-2015      Oleg Plakhotniuk
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

import json
import re
from datetime import datetime, timedelta

from woob.browser.exceptions import ServerError
from woob.browser.pages import HTMLPage, RawPage
from woob.capabilities.bank import Account, Transaction
from woob.tools.capabilities.bank.transactions import AmericanTransaction as AmTr
from woob.tools.date import closest_date
from woob.tools.pdf import decompress_pdf
from woob.tools.tokenizer import ReTokenizer


class SomePage(HTMLPage):
    @property
    def logged(self):
        return bool(self.doc.xpath('//a[text()="Logout"]'))


class SummaryPage(SomePage):
    def account(self):
        label = " ".join(self.doc.xpath('//div[contains(@class,"myCreditCardDetails")]')[0].text_content().split())
        balance = self.amount("Balance")
        cardlimit = (
            self.doc.xpath('//li[text()="Available to Spend"]')[0]
            .text_content()
            .replace("Available to Spend", "")
            .replace("Limit", "")
            .strip()
        )
        paymin = self.amount("Payment Due")
        if self.doc.xpath('//li[@class="noPaymentDue"]'):
            # If payment date is not scheduled yet, set it somewhere in a
            # distant future, so that we always have a valid date.
            paydate = datetime.now() + timedelta(days=999)
        else:
            rawtext = self.doc.xpath('//li[contains(text(),"Due Date")]')[0].text_content()
            datetext = re.match(r".*(\d{2}/\d{2}/\d{4}).*", rawtext).group(1)
            paydate = datetime.strptime(datetext, "%m/%d/%Y")
        a = Account()
        a.id = label[-4:]
        a.label = label
        a.currency = Account.get_currency(balance)
        a.balance = -AmTr.decimal_amount(balance)
        a.type = Account.TYPE_CARD
        a.cardlimit = AmTr.decimal_amount(cardlimit)
        a.paymin = AmTr.decimal_amount(paymin)
        if paydate is not None:
            a.paydate = paydate
        return a

    def amount(self, name):
        return (
            "".join(self.doc.xpath('//li[text()[.="%s"]]/../li[1]' % name)[0].text_content().split())
            .replace("\xb7", ".")
            .replace("*", "")
        )


class ActivityPage(SomePage):
    def iter_recent(self):
        records = json.loads(self.doc.xpath('//div[@id="completedActivityRecords"]//input[1]/@value')[0])
        recent = [x for x in records if x["PDF_LOC"] is None]
        for rec in sorted(recent, key=lambda rec: ActivityPage.parse_date(rec["TRANS_DATE"]), reverse=True):
            desc = " ".join(rec["TRANS_DESC"].split())
            trans = Transaction((rec["REF_NUM"] or "").strip())
            trans.date = ActivityPage.parse_date(rec["TRANS_DATE"])
            trans.rdate = ActivityPage.parse_date(rec["POST_DATE"])
            trans.type = Transaction.TYPE_UNKNOWN
            trans.raw = desc
            trans.label = desc
            trans.amount = -AmTr.decimal_amount(rec["TRANS_AMOUNT"])
            yield trans

    @staticmethod
    def parse_date(recdate):
        return datetime.strptime(recdate, "%B %d, %Y")


class StatementsPage(SomePage):
    def iter_statements(self):
        jss = self.doc.xpath('//a/@onclick[contains(.,"eBillViewPDFAction")]')
        for js in jss:
            url = re.match(r"window.open\('([^']*).*\)", js).group(1)
            for i in range(self.browser.MAX_RETRIES):
                try:
                    self.browser.location(url)
                    break
                except ServerError as e:
                    last_error = e
            else:
                raise last_error
            yield self.browser.page


class StatementPage(RawPage):
    LEX = [
        ("charge_amount", r"^\(\$(\d+(,\d{3})*\.\d{2})\) Tj$"),
        ("payment_amount", r"^\(\\\(\$(\d+(,\d{3})*\.\d{2})\\\)\) Tj$"),
        ("date", r"^\((\d+/\d+)\) Tj$"),
        ("full_date", r"^\((\d+/\d+/\d+)\) Tj$"),
        ("layout_td", r"^([-0-9]+ [-0-9]+) Td$"),
        ("ref", r"^\(([A-Z0-9]{17})\) Tj$"),
        ("text", r"^\((.*)\) Tj$"),
    ]

    def __init__(self, *args, **kwArgs):
        RawPage.__init__(self, *args, **kwArgs)
        assert self.doc[:4] == "%PDF"
        self._pdf = decompress_pdf(self.doc)
        self._tok = ReTokenizer(self._pdf, "\n", self.LEX)

    def iter_transactions(self):
        trs = self.read_transactions()
        # since the sorts are not in the same direction, we can't do in one pass
        # python sorting is stable, so sorting in 2 passes can achieve a multisort
        # the official docs give this way
        trs = sorted(trs, key=lambda tr: (tr.label, tr.amount))
        trs = sorted(trs, key=lambda tr: tr.date, reverse=True)
        return trs

    def read_transactions(self):
        # Statement typically cover one month.
        # Do 60 days, just to be on a safe side.
        date_to = self.read_closing_date()
        date_from = date_to - timedelta(days=60)

        pos = 0
        while not self._tok.tok(pos).is_eof():
            pos, trans = self.read_transaction(pos, date_from, date_to)
            if trans:
                yield trans
            else:
                pos += 1

    def read_transaction(self, pos, date_from, date_to):
        startPos = pos
        pos, tdate = self.read_date(pos)
        pos, pdate_layout = self.read_layout_td(pos)
        pos, pdate = self.read_date(pos)
        pos, ref_layout = self.read_layout_td(pos)
        pos, ref = self.read_ref(pos)
        pos, desc_layout = self.read_layout_td(pos)
        pos, desc = self.read_text(pos)
        pos, amount_layout = self.read_layout_td(pos)
        pos, amount = self.read_amount(pos)
        if tdate is None or pdate is None or desc is None or amount is None or amount == 0:
            return startPos, None
        else:
            tdate = closest_date(tdate, date_from, date_to)
            pdate = closest_date(pdate, date_from, date_to)
            desc = " ".join(desc.split())

            trans = Transaction(ref or "")
            trans.date = tdate
            trans.rdate = pdate
            trans.type = Transaction.TYPE_UNKNOWN
            trans.raw = desc
            trans.label = desc
            trans.amount = amount
            return pos, trans

    def read_amount(self, pos):
        pos, ampay = self.read_payment_amount(pos)
        if ampay is not None:
            return pos, ampay
        return self.read_charge_amount(pos)

    def read_charge_amount(self, pos):
        return self._tok.simple_read("charge_amount", pos, lambda xs: -AmTr.decimal_amount(xs[0]))

    def read_payment_amount(self, pos):
        return self._tok.simple_read("payment_amount", pos, lambda xs: AmTr.decimal_amount(xs[0]))

    def read_closing_date(self):
        pos = 0
        while not self._tok.tok(pos).is_eof():
            pos, text = self.read_text(pos)
            if text == "Statement Closing Date":
                break
            pos += 1
        while not self._tok.tok(pos).is_eof():
            pos, date = self.read_full_date(pos)
            if date is not None:
                return date
            pos += 1

    def read_text(self, pos):
        t = self._tok.tok(pos)
        # TODO: handle PDF encodings properly.
        return (pos + 1, str(t.value(), errors="ignore")) if t.is_text() else (pos, None)

    def read_full_date(self, pos):
        t = self._tok.tok(pos)
        return (pos + 1, datetime.strptime(t.value(), "%m/%d/%Y")) if t.is_full_date() else (pos, None)

    def read_date(self, pos):
        t = self._tok.tok(pos)
        return (pos + 1, datetime.strptime(t.value(), "%m/%d")) if t.is_date() else (pos, None)

    def read_ref(self, pos):
        t = self._tok.tok(pos)
        return (pos + 1, t.value()) if t.is_ref() else (pos, None)

    def read_layout_td(self, pos):
        t = self._tok.tok(pos)
        return (pos + 1, t.value()) if t.is_layout_td() else (pos, None)
