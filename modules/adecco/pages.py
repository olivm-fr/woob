# Copyright(C) 2013      Bezleputh
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

import requests

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.html import CleanHTML
from woob.browser.filters.javascript import JSVar
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, Date, Regexp
from woob.browser.pages import HTMLPage, JsonPage, pagination
from woob.capabilities.base import empty
from woob.capabilities.job import BaseJobAdvert


class SearchPage(HTMLPage):
    def get_post_params(self):
        return {
            "facetSettingId": JSVar(CleanText("//script"), var="_FacetName")(self.doc),
            "currentLanguage": JSVar(CleanText("//script"), var="_CurrentLanguage")(self.doc),
            "clientId": JSVar(CleanText("//script"), var="_ClientId")(self.doc),
            "branchId": JSVar(CleanText("//script"), var="_BranchId")(self.doc),
            "clientName": JSVar(CleanText("//script"), var="_ClientName")(self.doc),
        }


class AdvertsJsonPage(JsonPage):
    @pagination
    @method
    class iter_job_adverts(DictElement):
        item_xpath = "Items"

        def next_page(self):
            if len(self.page.doc["Pagination"]) >= 2:
                if self.page.doc["Pagination"][-2]["keyName"] == "Suivant":
                    url = self.page.doc["Pagination"][-2]["valueName"]
                    self.env["data"]["filterUrl"] = "http://www.adecco.fr%s" % url
                    return requests.Request("POST", self.page.url, data=self.env["data"])

        class item(ItemElement):
            klass = BaseJobAdvert

            def validate(self, advert):
                if empty(advert.publication_date) or not self.env["date_min"]:
                    return advert

                if advert.publication_date >= self.env["date_min"]:
                    return advert

            obj_id = Dict("JobId")
            obj_title = Dict("JobTitle")
            obj_place = Dict("JobLocation")
            obj_publication_date = Date(Dict("PostedDate"))


class AdvertPage(HTMLPage):
    @method
    class get_job_advert(ItemElement):
        klass = BaseJobAdvert

        def obj_id(self):
            _id = Regexp(CleanText('//meta[@property="og:url"]/@content'), r".*\?ID=(.*)", default=None)(self)
            if _id is None:
                _id = JSVar(CleanText("//script"), var="_JobDetailsId")(self)
            return _id

        def obj_title(self):
            title = CleanText('//meta[@property="og:title"]/@content', default=None)(self)
            if title is None:
                title = JSVar(CleanText("//script"), var="_JobTitle")(self)
            return title

        def obj_place(self):
            place = CleanText('//span[@itemprop="jobLocation"]', default=None)(self)
            if not place:
                place = CleanText('//li[@class="job--meta_location"]')(self)

            if not place:
                place = Regexp(CleanText('//meta[@property="og:title"]/@content'), r".*\ à (.*)")(self)
            return place

        def obj_publication_date(self):
            date = Date(CleanText('//time[@itemprop="startDate"]'), default=None)(self)
            if date is None:
                date = Date(CleanText('//span[@id="posted-date"]'))(self)
            return date

        obj_contract_type = CleanText(
            '//li[@class="job--meta_employment-type"]/div/div/span[@class="job-details-value"]'
        )

        # obj_pay = CleanText('//div[@class="jobGreyContain"]/div/div[4]/span[@class="value"]')

        def obj_job_name(self):
            job_name = Regexp(CleanText('//meta[@property="og:title"]/@content'), r"(.*)\|.*", default=None)(self)
            if job_name is None:
                job_name = JSVar(CleanText("//script"), var="_JobTitle")(self)
            return job_name

        obj_description = CleanHTML('//div[@class="VacancyDescription"]')

        def obj_url(self):
            url = CleanText('//meta[@property="og:url"]/@content', default=None)(self)
            if url is None:
                url = JSVar(CleanText("//script"), var="_JobUrl")(self)

            if not url.startswith("http"):
                url = "www.adecco.fr%s" % url

            return url
