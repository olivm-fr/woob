# Copyright(C) 2016      Simon Lipp
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

import datetime
import re

import dateutil.parser
import requests
from six.moves import urllib, xmlrpc_client

from woob.capabilities.messages import CapMessages, Message, Thread
from woob.exceptions import BrowserIncorrectPassword
from woob.tools.backend import BackendConfig, Module
from woob.tools.value import Value, ValueBackendPassword


__all__ = ["TapatalkModule"]


class TapatalkError(Exception):
    pass


class RequestsTransport:
    def __init__(self, uri):
        self._uri = uri
        self._session = requests.Session()

    def request(self, host, handler, request, verbose):
        response = self._session.post(self._uri, data=request, headers={"Content-Type": "text/xml; charset=UTF-8"})
        p, u = xmlrpc_client.getparser()
        p.feed(response.content)
        p.close()
        response.close()
        return u.close()


class TapatalkServerProxy(xmlrpc_client.ServerProxy):
    def __init__(self, uri):
        transport = RequestsTransport(uri)
        xmlrpc_client.ServerProxy.__init__(self, uri, transport)

    def __getattr__(self, name):
        method = xmlrpc_client.ServerProxy.__getattr__(self, name)
        return self._wrap(method)

    def _wrap(self, method):
        def call(*args, **kwargs):
            res = method(*args, **kwargs)
            if "result" in res and not res["result"]:
                raise TapatalkError(xmlrpc_str(res["result_text"]))
            return res

        return call


def xmlrpc_str(data):
    """
    Depending on how the XML-RPC server on the other end has been
    implemented, strings can either be str (or unicode in python2)
    or xmlrpc_client.Binary. Convert the later case in the former, and
    ensure that the result is always a str (even if the input is a number)
    """
    if isinstance(data, xmlrpc_client.Binary):
        return str(data.data, "utf-8")
    else:
        return str(data)


class TapatalkModule(Module, CapMessages):
    NAME = "tapatalk"
    DESCRIPTION = "Tapatalk-compatible sites"
    MAINTAINER = "Simon Lipp"
    EMAIL = "laiquo@hwold.net"
    LICENSE = "AGPLv3+"
    VERSION = "3.7"

    CONFIG = BackendConfig(
        Value("username", label="Username", default=""),
        ValueBackendPassword("password", label="Password", default=""),
        Value("url", label="Site URL", default="https://support.tapatalk.com/mobiquo/mobiquo.php"),
        Value(
            "message_url_format",
            label="Message URL format",
            default="/index.php?/topic/{thread_id}-{thread_title}#entry{message_id}",
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._xmlrpc_client = None

    @property
    def _conn(self):
        if self._xmlrpc_client is None:
            url = self.config["url"].get().rstrip("/")
            username = self.config["username"].get()
            password = self.config["password"].get()
            self._xmlrpc_client = TapatalkServerProxy(url)
            try:
                self._xmlrpc_client.login(username, password)
            except TapatalkError as e:
                raise BrowserIncorrectPassword(e.message)
        return self._xmlrpc_client

    def _get_time(self, post):
        if "post_time" in post:
            return dateutil.parser.parse(xmlrpc_str(post["post_time"]))
        else:
            return datetime.datetime.now()

    def _format_content(self, post):
        msg = xmlrpc_str(post["post_content"])
        msg = re.sub(r"\[url=(.+?)\](.*?)\[/url\]", r'<a href="\1">\2</a>', msg)
        msg = re.sub(r"\[quote\s?.*\](.*?)\[/quote\]", r"<blockquote><p>\1</p></blockquote>", msg)
        msg = re.sub(r"\[img\](.*?)\[/img\]", r'<img src="\1">', msg)
        if post.get("icon_url"):
            return '<img style="float:right;position:relative" src="{}"> {}'.format(xmlrpc_str(post["icon_url"]), msg)
        else:
            return msg

    def _process_post(self, thread, post, is_root):
        message_id = is_root and "0" or xmlrpc_str(post["post_id"])
        message_title = is_root and thread.title or "Re: %s" % thread.title

        # Tapatalk app seems to have hardcoded this construction... I don't think we can do better :(
        rel_url = (
            self.config["message_url_format"]
            .get()
            .format(
                thread_id=urllib.parse.quote(thread.id.encode("utf-8")),
                thread_title=urllib.parse.quote(thread.title.encode("utf-8")),
                message_id=urllib.parse.quote(message_id.encode("utf-8")),
                message_title=urllib.parse.quote(message_title.encode("utf-8")),
            )
        )

        message = Message(
            id=message_id,
            thread=thread,
            sender=xmlrpc_str(post.get("post_author_name", "Anonymous")),
            title=message_title,
            url=urllib.parse.urljoin(self.config["url"].get(), rel_url),
            receivers=None,
            date=self._get_time(post),
            content=self._format_content(post),
            signature=None,
            parent=thread.root or None,
            children=[],
            flags=Message.IS_HTML,
        )

        if thread.root:
            thread.root.children.append(message)
        elif is_root:
            thread.root = message
        else:
            # First message in the thread is not the root message,
            # because we asked only for unread messages. Create a non-loaded root
            # message to allow monboob to fill correctly the References: header
            thread.root = Message(id="0", parent=None, children=[message], thread=thread)
            message.parent = thread.root

        return message

    def fill_thread(self, thread, fields, unread=False):
        def fill_root(thread, start, count, first_unread):
            while True:
                topic = self._conn.get_thread(thread.id, start, start + count - 1, True)
                for i, post in enumerate(topic["posts"]):
                    message = self._process_post(thread, post, start * count + i == 0)
                    if start + i >= first_unread:
                        message.flags |= Message.IS_UNREAD

                start += count
                if start >= topic["total_post_num"]:
                    return thread

        count = 50
        topic = self._conn.get_thread_by_unread(thread.id, count)
        if "title" in fields:
            thread.title = xmlrpc_str(topic["topic_title"])
        if "date" in fields:
            thread.date = self._get_time(topic)
        if "root" in fields:
            # "position" starts at 1, whereas the "start" argument of get_thread starts at 0
            pos = topic["position"] - 1
            if unread:
                # start must be on a page boundary, or various (unpleasant) things will happen,
                # like get_threads returning nothing
                start = (pos // count) * count
                thread = fill_root(thread, start, count, pos)
            else:
                thread = fill_root(thread, 0, count, pos)

        return thread

    #### CapMessages ##############################################

    def get_thread(self, id):
        return self.fill_thread(Thread(id), ["title", "root", "date"])

    def iter_threads(self, unread=False):
        def browse_forum_mode(forum, prefix, mode):
            start = 0
            count = 50
            while True:
                if mode:
                    topics = self._conn.get_topic(xmlrpc_str(forum["forum_id"]), start, start + count - 1, mode)
                else:
                    topics = self._conn.get_topic(xmlrpc_str(forum["forum_id"]), start, start + count - 1)

                all_ignored = True
                for topic in topics["topics"]:
                    t = Thread(xmlrpc_str(topic["topic_id"]))
                    t.title = xmlrpc_str(topic["topic_title"])
                    t.date = self._get_time(topic)
                    if not unread or topic.get("new_post"):
                        all_ignored = False
                        yield t
                start += count
                if start >= topics["total_topic_num"] or all_ignored:
                    break

        def process_forum(forum, prefix):
            if (not unread or forum.get("new_post", True)) and not forum["sub_only"]:
                for mode in ("TOP", "ANN", None):
                    yield from browse_forum_mode(forum, prefix, mode)

            for child in forum.get("child", []):
                yield from process_forum(child, "{}.{}".format(prefix, xmlrpc_str(child["forum_name"])))

        for forum in self._conn.get_forum():
            yield from process_forum(forum, xmlrpc_str(forum["forum_name"]))

    def iter_unread_messages(self):
        for thread in self.iter_threads(unread=True):
            self.fill_thread(thread, ["root"], unread=True)
            for message in thread.iter_all_messages():
                if message.flags & Message.IS_UNREAD:
                    yield message

    def set_message_read(self, message):
        # No-op: the underlying forum will mark topics as read as we read them
        pass

    OBJECTS = {Thread: fill_thread}
