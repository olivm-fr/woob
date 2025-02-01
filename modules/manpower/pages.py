# -*- coding: utf-8 -*-

# Copyright(C) 2016      Bezleputh
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.
from datetime import date

from woob.browser.elements import ItemElement, ListElement, method
from woob.browser.filters.html import CleanHTML
from woob.browser.filters.standard import BrowserURL, CleanText, Date, Env, Format, Regexp
from woob.browser.pages import HTMLPage
from woob.capabilities.base import NotAvailable
from woob.capabilities.job import BaseJobAdvert


class SearchPage(HTMLPage):
    @method
    class iter_job_adverts(ListElement):
        item_xpath = '//div[has-class("item")]'

        class item(ItemElement):
            klass = BaseJobAdvert

            obj_id = Regexp(
                CleanText('./div/a[@class="title-link"]/@href'), "/candidats/detail-offre-d-emploi/(.*).html"
            )
            obj_title = CleanText('./div/a[@class="title-link"]/h2')

            def obj_place(self):
                content = CleanText("./div[2]")(self)
                if len(content.split("|")) > 1:
                    return content.split("|")[1]
                return ""

            def obj_publication_date(self):
                content = CleanText("./div[2]")(self)
                split_date = content.split("|")[0].split("/")
                if len(split_date) == 3:
                    return date(int(split_date[2]) + 2000, int(split_date[1]), int(split_date[0]))
                return ""


class AdvertPage(HTMLPage):
    @method
    class get_job_advert(ItemElement):
        klass = BaseJobAdvert

        obj_id = Env("_id")
        obj_url = BrowserURL("advert_page", _id=Env("_id"))
        obj_title = CleanText('//div[@class="infos-lieu"]/h1')
        obj_place = CleanText('//div[@class="infos-lieu"]/h2')
        obj_publication_date = Date(
            Regexp(CleanText('//div[@class="info-agency"]'), r".*Date de l\'annonce :(.*)", default="")
        )
        obj_job_name = CleanText('//div[@class="infos-lieu"]/h1')
        obj_description = Format(
            "\n%s%s", CleanHTML('//article[@id="post-description"]/div'), CleanHTML('//article[@id="poste"]')
        )
        obj_contract_type = Regexp(
            CleanText('//article[@id="poste"]/div/ul/li'), r"Contrat : (\w*)", default=NotAvailable
        )
        obj_pay = Regexp(
            CleanText('//article[@id="poste"]/div/ul/li'), r"Salaire : (.*) par mois", default=NotAvailable
        )
        obj_experience = Regexp(
            CleanText('//article[@id="poste"]/div/ul/li'), r"Expérience : (.* ans)", default=NotAvailable
        )
