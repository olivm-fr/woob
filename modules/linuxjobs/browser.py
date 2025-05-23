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

from urllib.parse import quote_plus

from woob.browser import URL, PagesBrowser

from .pages import AdvertPage, SearchPage


class LinuxJobsBrowser(PagesBrowser):
    BASEURL = "https://www.linuxjobs.fr"

    advert_page = URL("/jobs/(?P<id>.+)", AdvertPage)
    search_page = URL("/search/(?P<job>)", SearchPage)

    def get_job_advert(self, _id, advert):
        self.advert_page.go(id=_id)

        assert self.advert_page.is_here()
        return self.page.get_job_advert(obj=advert)

    def search_job(self, pattern=None):
        if pattern is None:
            return []
        self.search_page.go(job=quote_plus(pattern.encode("utf-8")))

        assert self.search_page.is_here()
        return self.page.iter_job_adverts()
