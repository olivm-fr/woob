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

from woob.browser import URL, LoginBrowser, need_login
from woob.capabilities.messages import Message
from woob.exceptions import BrowserIncorrectPassword

from .pages import (
    HomeTimelinePage,
    LoginErrorPage,
    LoginPage,
    SearchPage,
    SearchTimelinePage,
    ThreadPage,
    TimelinePage,
    TrendsPage,
    Tweet,
)


__all__ = ["TwitterBrowser"]


class TwitterBrowser(LoginBrowser):
    BASEURL = "https://twitter.com/"

    authenticity_token = None

    thread_page = URL(r"(?P<user>.+)/status/(?P<_id>.+)", ThreadPage)
    login_error = URL(r"login/error.+", LoginErrorPage)
    tweet = URL("i/tweet/create", Tweet)
    trends = URL(r"i/trends\?pc=true&show_context=false&src=search-home&k=(?P<token>.*)", TrendsPage)
    search = URL("i/search/timeline", SearchTimelinePage)
    search_page = URL(r"search\?q=(?P<pattern>.+)&src=sprv", "search-home", SearchPage)
    profil = URL(r"i/profiles/show/(?P<path>.+)/timeline/tweets", HomeTimelinePage)
    timeline = URL("i/timeline", TimelinePage)
    login = URL("", LoginPage)

    def do_login(self):
        self.login.stay_or_go()

        if not self.authenticity_token:
            self.authenticity_token = self.page.login(self.username, self.password)

        if not self.page.logged or self.login_error.is_here():
            raise BrowserIncorrectPassword()

    @need_login
    def get_me(self):
        return self.login.stay_or_go().get_me()

    @need_login
    def iter_threads(self):
        return self.timeline.go().iter_threads()

    def get_trendy_subjects(self):
        if self.username:
            return self.get_logged_trendy_subject()
        else:
            return self.trends.open(token="").get_trendy_subjects()

    def get_logged_trendy_subject(self):
        if not self.authenticity_token:
            self.do_login()

        trends_token = self.search_page.open().get_trends_token()
        return self.trends.open(token=trends_token).get_trendy_subjects()

    @need_login
    def post(self, thread, message):
        datas = {"place_id": "", "tagged_users": ""}
        datas["authenticity_token"] = self.authenticity_token
        datas["status"] = message
        if thread:
            datas["in_reply_to_status_id"] = thread.id.split("#")[-1]

        self.tweet.open(data=datas)

    def get_thread(self, _id, thread=None, seen=None):
        splitted_id = _id.split("#")

        if not thread:
            thread = self.thread_page.go(_id=splitted_id[1].split(".")[-1], user=splitted_id[0]).get_thread(obj=thread)

        title_content = thread.title.split("\n\t")[-1]

        thread.root = Message(
            thread=thread,
            id=splitted_id[1].split(".")[-1],
            title=title_content[:50] if len(title_content) > 50 else title_content,
            sender=splitted_id[0],
            receivers=None,
            date=thread.date,
            parent=thread.root,
            content=title_content,
            signature="",
            children=[],
        )

        if seen and (_id not in seen):
            thread.root.flags = Message.IS_UNREAD

        comments = self.thread_page.stay_or_go(_id=splitted_id[1].split(".")[-1], user=splitted_id[0]).iter_comments()
        for comment in comments:
            comment.thread = thread
            comment.parent = thread.root
            if seen and comment.id not in seen.keys():
                comment.flags = Message.IS_UNREAD

            thread.root.children.append(comment)

        return thread

    def get_tweets_from_profil(self, path):
        return self.profil.go(path=path).iter_threads()

    def get_tweets_from_hashtag(self, path):
        return self.get_tweets_from_search("#%s" % path if not path.startswith("#") else path)

    def get_tweets_from_search(self, path):
        min_position = self.search_page.go(pattern=path).get_min_position()
        params = {"q": "%s" % path, "src": "sprv"}

        return self.search.go(params=params).iter_threads(params=params, min_position=min_position)
