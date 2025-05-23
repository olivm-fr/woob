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

from urllib.parse import quote_plus, urlencode

from woob.browser import URL, PagesBrowser

from .pages import AdvertPage, AdvSearchPage, ExpiredAdvert


__all__ = ["MonsterBrowser"]


class MonsterBrowser(PagesBrowser):
    BASEURL = "https://www.monster.fr"

    advert = URL(r"https://offre-demploi\.monster\.fr/v2/job/View\?JobID=(?P<_id>.*)", AdvertPage)
    expired_advert = URL(r"https://offre-demploi\.monster\.fr/v2/job/Expired\?JobId=(?P<_id>.*)", ExpiredAdvert)
    adv_search = URL(r"/emploi/recherche/(?P<search>.*)&page=(?P<page>\d*)", AdvSearchPage)

    def search_job(self, pattern=None):
        return self.adv_search.go(search="?q=%s" % quote_plus(pattern), page=1).iter_job_adverts()

    def advanced_search_job(self, job_name, place, contract, limit_date):
        search = "" if not contract else contract
        query = {"q": quote_plus(job_name), "where": place, "tm": limit_date}
        return self.adv_search.go(search=f"{search}?{urlencode(query)}", page=1).iter_job_adverts()

    def get_job_advert(self, _id, advert):
        return self.advert.go(_id=_id).get_job_advert(obj=advert)
