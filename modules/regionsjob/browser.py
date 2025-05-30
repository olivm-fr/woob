# Copyright(C) 2014      Bezleputh
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

from woob.browser import URL, PagesBrowser

from .pages import AdvertPage, LocationPage, SearchPage


__all__ = ["RegionsjobBrowser"]


class RegionsjobBrowser(PagesBrowser):

    search_page = URL(r"emplois/recherche\.html\?.*", SearchPage)
    advert_page = URL(r"emplois/(?P<_id>.*)\.html", AdvertPage)
    location_page = URL(r"search/getloc\?term=(?P<place>.*)", LocationPage)

    def __init__(self, website, *args, **kwargs):
        self.BASEURL = "https://%s/" % website
        PagesBrowser.__init__(self, *args, **kwargs)

    def search_job(
        self,
        pattern="",
        fonction="",
        secteur="",
        contract="",
        experience="",
        qualification="",
        enterprise_type="",
        place="",
    ):

        params = {"k": pattern.encode("utf-8")}

        if fonction:
            params["f"] = fonction

        if qualification:
            params["q"] = qualification

        if contract:
            params["c"] = contract

        if experience:
            params["e"] = experience

        if secteur:
            params["s"] = secteur

        if enterprise_type:
            params["et"] = enterprise_type

        if place:
            location = self.location_page.go(place=place).get_location()
            params["l"] = location

        return self.search_page.go(params=params).iter_job_adverts(domain=self.BASEURL)

    def get_job_advert(self, _id, advert):
        splitted_id = _id.split("#")
        self.BASEURL = "https://www.%s.com/" % splitted_id[0]
        return self.advert_page.go(_id=splitted_id[1]).get_job_advert(obj=advert)
