# Copyright(C) 2016      Edouard Lambert
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
from decimal import Decimal
from functools import wraps
from uuid import uuid4

from woob.browser.browsers import APIBrowser
from woob.browser.exceptions import ClientError
from woob.browser.filters.html import ReplaceEntities
from woob.browser.filters.standard import CleanDecimal, Coalesce, Date, MapIn
from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import NotAvailable
from woob.exceptions import ActionNeeded, BrowserIncorrectPassword
from woob.tools.capabilities.bank.investments import is_isin_valid


def need_login(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.users is None:
            self.do_login()
        return func(self, *args, **kwargs)

    return wrapper


class YomoniBrowser(APIBrowser):
    BASEURL = "https://yomoni.fr"

    ACCOUNT_TYPES = {
        "assurance vie": Account.TYPE_LIFE_INSURANCE,
        "compte titre": Account.TYPE_MARKET,
        "pea": Account.TYPE_PEA,
        "per": Account.TYPE_PER,
    }

    def __init__(self, username, password, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username = username
        self.password = password
        self.users = None
        self.accounts = []
        self.investments = {}
        self.histories = {}
        self.request_headers = {}

    def build_request(self, *args, **kwargs):
        if "data" in kwargs:
            kwargs["data"] = json.dumps(kwargs["data"])
        if "headers" not in kwargs:
            kwargs["headers"] = {}
        kwargs["headers"]["Content-Type"] = "application/vnd.yomoni.v1+json; charset=UTF-8"

        return super(APIBrowser, self).build_request(*args, **kwargs)

    def do_login(self):
        data = {
            "username": self.username,
            "password": self.password,
        }
        try:
            response = self.open("auth/login", data=data, headers={"X-Request-Id": str(uuid4().hex)[0:11]})
            self.request_headers["api_token"] = response.headers["API_TOKEN"]
            self.users = response.json()
        except ClientError:
            raise BrowserIncorrectPassword()

    waiting_statuses = (
        "RETURN_CUSTOMER_SERVICE",
        "SUBSCRIPTION_STEP_2",
        "SUBSCRIPTION_STEP_3",
        "SUBSCRIPTION_STEP_4",
    )

    @need_login
    def iter_accounts(self):
        if self.accounts:
            yield from self.accounts
            return

        waiting = False
        for project in self.users["projects"]:
            self.open("/user/{}/project/{}/".format(self.users["userId"], project["projectId"]), method="OPTIONS")
            me = self.request(
                "/user/{}/project/{}/".format(self.users["userId"], project["projectId"]), headers=self.request_headers
            )

            waiting = me["status"] in self.waiting_statuses

            # Check project in progress
            if not me["numeroContrat"] or not me["dateAdhesion"] or not me["solde"]:
                continue

            a = Account()
            a.id = "".join(me["numeroContrat"].split())
            a.number = me["numeroContrat"]
            a.opening_date = Date(default=NotAvailable).filter(me.get("dateAdhesion"))
            a.label = " ".join(me["supportEpargne"].split("_"))
            a.type = MapIn(self, self.ACCOUNT_TYPES, Account.TYPE_UNKNOWN).filter(a.label.lower())
            a.balance = CleanDecimal().filter(me["solde"])
            a.currency = "EUR"  # performanceEuro, montantEuro everywhere in Yomoni JSON
            a.iban = me["ibancompteTitre"] or NotAvailable
            a._project_id = project["projectId"]
            a.valuation_diff = CleanDecimal().filter(me["performanceEuro"])
            a._startbalance = me["montantDepart"]

            self.accounts.append(a)

            self.iter_investment(a, me["sousJacents"])

            yield a

        if not self.accounts and waiting:
            raise ActionNeeded(
                locale="fr-FR",
                message="Le service client Yomoni est en attente d'un retour de votre part.",
            )

    @need_login
    def iter_investment(self, account, invs=None):
        if account.id not in self.investments and invs is not None:
            self.investments[account.id] = []
            for inv in invs:
                i = Investment()
                # If nothing is given to make the label, we use the ISIN instead
                # We let it crash if the ISIN is not available either.
                if all([inv["classification"], inv["description"]]):
                    i.label = "{} - {}".format(inv["classification"], inv["description"])
                else:
                    i.label = Coalesce().filter(
                        (
                            inv["classification"],
                            inv["description"],
                            inv["isin"],
                        )
                    )
                i.code = inv["isin"]
                if not is_isin_valid(i.code):
                    i.code = NotAvailable
                    i.code_type = NotAvailable
                    if "Solde Espèces" in i.label:
                        i.code = "XX-liquidity"
                else:
                    i.code_type = Investment.CODE_TYPE_ISIN

                i.quantity = CleanDecimal(default=NotAvailable).filter(inv["nombreParts"])
                i.unitprice = CleanDecimal(default=NotAvailable).filter(inv["prixMoyenAchat"])
                i.unitvalue = CleanDecimal(default=NotAvailable).filter(inv["valeurCotation"])
                i.valuation = round(Decimal(inv["montantEuro"]), 2)
                # For some invests the vdate returned is None
                # Consequently we set the default value at NotAvailable
                i.vdate = Date(default=NotAvailable).filter(inv["datePosition"])
                i.diff = CleanDecimal(default=NotAvailable).filter(inv["performanceEuro"])

                self.investments[account.id].append(i)
        return self.investments[account.id]

    @need_login
    def iter_history(self, account):
        if account.id not in self.histories:
            histories = []
            self.open(
                "/user/{}/project/{}/activity".format(self.users["userId"], account._project_id), method="OPTIONS"
            )
            for activity in [
                acc
                for acc in self.request(
                    "/user/{}/project/{}/activity".format(self.users["userId"], account._project_id),
                    headers=self.request_headers,
                )["activities"]
                if acc["details"] is not None
            ]:

                m = re.search(
                    r"([\d\, ]+)(?=[\s]+€|[\s]+euro)",
                    ReplaceEntities().filter(activity["details"]),
                    flags=re.UNICODE,
                )

                if "Souscription" not in activity["title"] and not m:
                    continue

                t = Transaction()
                t.label = "{} - {}".format(" ".join(activity["type"].split("_")), activity["title"])
                t.date = Date().filter(activity["date"])
                t.type = Transaction.TYPE_BANK
                amount = (
                    account._startbalance
                    if not m
                    else "-%s" % m.group(1) if "FRAIS" in activity["type"] else m.group(1)
                )
                t.amount = CleanDecimal(replace_dots=True).filter(amount)

                histories.append(t)

            self.histories[account.id] = histories
        return self.histories[account.id]
