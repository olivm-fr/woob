from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest import TestCase

from woob.capabilities.bank import Account

from ..pages.accounts_list import AccountApiPage, AccountHistoryPage, TransactionApiPage


class MockBrowser:
    logger = None


class TestAccountApiPage(TestCase):
    def test_fill_account(self):
        response = type(
            "MockResponse",
            (),
            {
                "url": "https://api.fortuneo.fr/accounts/123/balance",
                "headers": {"content-type": "application/json; charset=utf-8"},
                "text": json.dumps({"balances": [{"amount": {"value": "1234.56"}}], "currency": "EUR"}),
            },
        )()

        page = AccountApiPage(MockBrowser(), response)
        account = Account()
        page.fill_account(account)

        self.assertEqual(account.balance, Decimal("1234.56"))
        self.assertEqual(account.currency, "EUR")


class TestTransactionApiPage(TestCase):
    def test_iter_history(self):
        data = [
            {
                "bookingDate": "2024-01-15",
                "valueDate": "2024-01-14",
                "transactionDate": "2024-01-13",
                "label": {"originalLabel": "VIR SEPA FROM CLIENT", "simplifiedLabel": "VIR FROM CLIENT"},
                "amount": {"value": "-42.50"},
            },
            {
                "bookingDate": "2024-01-10",
                "valueDate": "2024-01-10",
                "transactionDate": "2024-01-10",
                "label": {"originalLabel": "ZERO TRN", "simplifiedLabel": "ZERO TRN"},
                "amount": {"value": "0"},
            },
        ]
        response = type(
            "MockResponse",
            (),
            {
                "url": "https://api.fortuneo.fr/accounts/123/transactions",
                "headers": {"content-type": "application/json; charset=utf-8"},
                "text": json.dumps(data),
            },
        )()

        page = TransactionApiPage(MockBrowser(), response)
        transactions = list(page.iter_history())

        self.assertEqual(len(transactions), 1)
        tr = transactions[0]
        self.assertEqual(tr.date, date(2024, 1, 15))
        self.assertEqual(tr.vdate, date(2024, 1, 14))
        self.assertEqual(tr.rdate, date(2024, 1, 13))
        self.assertEqual(tr.raw, "VIR SEPA FROM CLIENT")
        self.assertEqual(tr.label, "VIR FROM CLIENT")
        self.assertEqual(tr.amount, Decimal("-42.50"))
        self.assertIsNone(tr._details_link)


class TestAccountHistoryPage(TestCase):
    def test_get_account_api_id(self):
        html = b"""<!DOCTYPE html>
<html><head></head><body>
<script>
var config = {
    accountId: 'abc123',
    other: 'value'
};
</script>
</body></html>"""

        response = type(
            "MockResponse",
            (),
            {
                "url": "https://mabanque.fortuneo.fr/fr/prive/mes-comptes/compte-courant/consulter-situation.jsp",
                "headers": {"content-type": "text/html; charset=utf-8"},
                "text": html.decode("utf-8"),
                "content": html,
                "encoding": "utf-8",
            },
        )()

        page = AccountHistoryPage(MockBrowser(), response)
        account_id = page.get_account_api_id()

        self.assertEqual(account_id, "abc123")
