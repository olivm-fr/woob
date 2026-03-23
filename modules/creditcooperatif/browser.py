# Copyright(C) 2012 Kevin Pouget
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

from woob.browser import URL
from woob_modules.banquepopulaire.browser import BanquePopulaire


__all__ = ["CreditCooperatif"]


class CreditCooperatif(BanquePopulaire):
    BASEURL = "https://www.credit-cooperatif.coop"
    # Base URLs names from :class:`ConstPage` content
    URL_ICG = "https://www.icgauth.credit-cooperatif.coop"
    URL_RS_AUTH = "https://www.rs-ext-bad-ce.credit-cooperatif.coop"
    GW_AS_ENDPOINT_PAS = "https://www.as-ext-bad-ce.credit-cooperatif.coop"
    INFO_TOKEN_ENDPOINT = "https://www.as-ano-bad-ce.caisse-epargne.fr"

    CDETAB = "42559"
    ENSEIGNE = "ccoop"
    SNID = "224838"

    redirect_uri = URL("https://www.net255.credit-cooperatif.coop/loginbel.aspx")

    @property
    def cdetab(self):
        return self.CDETAB

    @cdetab.setter
    def cdetab(self, value):
        # Parent loads from config. This is ignored here
        pass

    @property
    def info_token_headers(self):
        return {
            "Accept": "application/json, text/plain, */*",
            # Mandatory, else you've got an HTML page.
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.icgauth.credit-cooperatif.coop/",
            "Origin": "https://www.icgauth.credit-cooperatif.coop",
        }

    def get_claims(self):
        return {**super().get_claims(), "bpce_session_id": None, "idpid": None}
