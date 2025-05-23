# Copyright(C) 2011-2021 Romain Bignon
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

from woob.capabilities.base import NotLoaded
from woob.capabilities.paste import PasteNotFound
from woob.tools.test import BackendTest, SkipTest

from .browser import LimitExceeded


class PastebinTest(BackendTest):
    MODULE = "pastebin"

    def test_get_paste(self):
        for _id in ("7HmXwzyt", "https://pastebin.com/7HmXwzyt"):
            # html method
            p = self.backend.get_paste(_id)
            self.backend.fillobj(p, ["title"])
            assert p.title == "plop"
            assert p.page_url == "https://pastebin.com/7HmXwzyt"
            assert p.contents == "prout"
            assert p.public is True
            assert p._date.year == 2011

            # raw method
            p = self.backend.get_paste(_id)
            self.backend.fillobj(p, ["contents"])
            assert p.title is NotLoaded
            assert p.page_url is None
            assert p.contents == "prout"
            assert p.public is NotLoaded

    def test_post(self):
        # we cannot test public pastes, as the website sometimes forces them as private
        # there seems to be a very low post per day limit, even when logged in
        p = self.backend.new_paste(None, title="ouiboube", contents="Woob Test", public=False)
        try:
            self.backend.post_paste(p, max_age=600)
        except LimitExceeded:
            raise SkipTest("Limit exceeded")
        assert p.id
        assert not p.id.startswith("https://")
        self.backend.fill_paste(p, ["title"])
        assert p.title == "ouiboube"
        assert p.id in p.page_url
        assert p.public is False

    def test_specialchars(self):
        # post a paste and get the contents through the HTML response
        p1 = self.backend.new_paste(None, title="ouiboube", contents="Woob <test>¿¡", public=False)
        try:
            self.backend.post_paste(p1, max_age=600)
        except LimitExceeded:
            raise SkipTest("Limit exceeded")
        assert p1.id
        # not related to testing special chars, but check if the paste is
        # really private since test_post() tests the contrary
        assert p1.public is False

        # this should use the raw method to get the contents
        p2 = self.backend.get_paste(p1.id)
        self.backend.fillobj(p2, ["contents"])
        assert p2.contents == p1.contents
        assert p2.public is NotLoaded

    def test_notfound(self):
        for _id in ("weboooooooooooooooooooooooooob", "https://pastebin.com/weboooooooooooooooooooooooooob"):
            # html method
            p = self.backend.get_paste(_id)
            self.assertRaises(PasteNotFound, self.backend.fillobj, p, ["title"])

            # raw method
            p = self.backend.get_paste(_id)
            self.assertRaises(PasteNotFound, self.backend.fillobj, p, ["contents"])

    def test_checkurl(self):
        # call with an URL we can't handle with this backend
        assert self.backend.get_paste("http://paste.alacon.org/1") is None

    def test_can_post(self):
        assert self.backend.can_post("hello", public=None) > 0
        assert self.backend.can_post("hello", public=True) > 0
        assert self.backend.can_post("hello", public=False) > 0
        assert self.backend.can_post("hello", public=True, max_age=600) > 0
        assert self.backend.can_post("hello", public=True, max_age=3600 * 24) > 0
        assert self.backend.can_post("hello", public=True, max_age=3600 * 24 * 3) > 0
        assert self.backend.can_post("hello", public=True, max_age=False) > 0
        assert self.backend.can_post("hello", public=None, max_age=False) > 0
        assert self.backend.can_post("hello", public=True, max_age=3600 * 24 * 40) > 0
        assert self.backend.can_post("héhé", public=True) > 0
        assert self.backend.can_post("hello ♥", public=True) > 0
