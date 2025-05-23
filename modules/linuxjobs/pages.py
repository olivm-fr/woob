# Copyright(C) 2016      François Revol
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


from woob.browser.elements import ItemElement, ListElement, method
from woob.browser.filters.html import CleanHTML, Link
from woob.browser.filters.standard import BrowserURL, CleanText, Date, Env, Regexp
from woob.browser.pages import HTMLPage
from woob.capabilities.job import BaseJobAdvert
from woob.tools.date import parse_french_date


class AdvertPage(HTMLPage):
    @method
    class get_job_advert(ItemElement):
        klass = BaseJobAdvert

        obj_id = Env("id")
        obj_url = BrowserURL("advert_page", id=Env("id"))
        obj_title = CleanText("//title")
        obj_job_name = CleanText("//title")
        obj_society_name = CleanText('//div[2]/div[@class="col-md-9"]/h4[1]')
        obj_publication_date = Date(
            CleanText('//div[2]/div[@class="col-md-9"]/small', replace=[("Ajoutée le", "")]),
            parse_func=parse_french_date,
        )
        obj_place = Regexp(CleanText('//div[2]/div[@class="col-md-9"]/h4[2]'), r"(.*) \(.*\)")
        obj_description = CleanHTML('//div[4]/div[@class="col-md-9"]')


class SearchPage(HTMLPage):
    @method
    class iter_job_adverts(ListElement):
        item_xpath = '//a[@class="list-group-item "]'

        class item(ItemElement):
            klass = BaseJobAdvert

            obj_id = Regexp(Link("."), r".*fr/jobs/(\d+)/.*")
            obj_title = CleanText('h4/span[@class="job-title"]')
            obj_society_name = CleanText('h4/span[@class="job-company"]')
            obj_publication_date = Date(CleanText('h4/span[@class="badge pull-right"]'), parse_func=parse_french_date)
