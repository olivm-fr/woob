# Copyright(C) 2014 Vicnet
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

from .pages import AdvertPage, ListingAutoPage


__all__ = ["LaCentraleBrowser"]


class LaCentraleBrowser(PagesBrowser):
    BASEURL = "http://www.lacentrale.fr"

    list_page = URL(r"/listing_auto\.php\?(?P<_request>.*)", ListingAutoPage)
    advert_page = URL(r"/auto-occasion-annonce-(?P<_id>.*).html", AdvertPage)

    def iter_prices(self, product):
        _request = "&".join([f"{key}={item}" for key, item in product._criteria.items()])
        return self.list_page.go(_request=_request).iter_prices()

    def get_price(self, _id, obj):
        return self.advert_page.go(_id=_id).get_price(obj=obj)
