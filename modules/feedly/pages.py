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

from datetime import datetime

from woob.browser.elements import DictElement, ItemElement, method
from woob.browser.filters.html import CleanHTML
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import CleanText, Format
from woob.browser.pages import JsonPage, LoggedPage
from woob.capabilities.collection import Collection
from woob.capabilities.messages import Message


class ContentsPage(LoggedPage, JsonPage):

    @method
    class get_articles(DictElement):
        item_xpath = "items"

        class item(ItemElement):
            klass = Message

            obj_id = Format("%s#%s", CleanText(Dict("origin/streamId")), CleanText(Dict("id")))
            obj_sender = CleanText(Dict("author", default=""))
            obj_title = Format(
                "%s - %s", CleanText(Dict("origin/title", default="")), CleanText(Dict("title", default=""))
            )

            def obj_date(self):
                return datetime.fromtimestamp(Dict("published")(self.el) / 1e3)

            def obj_content(self):
                if "content" in self.el.keys():
                    return Format("%s%s\r\n", CleanHTML(Dict("content/content")), CleanText(Dict("origin/htmlUrl")))(
                        self.el
                    )
                elif "summary" in self.el.keys():
                    return Format("%s%s\r\n", CleanHTML(Dict("summary/content")), CleanText(Dict("origin/htmlUrl")))(
                        self.el
                    )
                else:
                    return ""


class TokenPage(JsonPage):
    def get_token(self):
        return self.doc["access_token"], self.doc["id"]


class EssentialsPage(JsonPage):
    def get_categories(self):
        for category in self.doc:
            name = "%s" % category.get("label")
            yield Collection([name], name)

    def get_feeds(self, label):
        for category in self.doc:
            if category.get("label") == label:
                feeds = category.get("subscriptions")
                for feed in feeds:
                    yield Collection([label, feed.get("title")])

    def get_feed_url(self, _category, _feed):
        for category in self.doc:
            if category.get("label") == _category:
                feeds = category.get("subscriptions")
                for feed in feeds:
                    if feed.get("title") == _feed:
                        return feed.get("id")


class PreferencesPage(LoggedPage, JsonPage):
    def get_categories(self):
        for category, value in self.doc.items():
            if value in ["shown", "hidden"]:
                yield Collection(["%s" % category], "%s" % category.replace("global.", ""))


class MarkerPage(LoggedPage):
    pass
