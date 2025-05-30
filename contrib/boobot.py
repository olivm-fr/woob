#!/usr/bin/env python

# Copyright(C) 2012  Romain Bignon
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

import itertools
import logging
import os
import re
import sys
import urllib
import urllib.parse as urlparse
from datetime import datetime, timedelta
from math import log
from random import choice, randint
from threading import Event, Thread

from dateutil.parser import parse as parse_date
from irc.bot import SingleServerIRCBot

from woob.browser import Browser
from woob.browser.exceptions import HTTPNotFound
from woob.browser.pages import HTMLPage
from woob.core import Woob
from woob.exceptions import BrowserHTTPError, BrowserUnavailable
from woob.tools.application.base import ApplicationStorage
from woob.tools.misc import get_backtrace, to_unicode
from woob.tools.storage import StandardStorage


IRC_CHANNELS = os.getenv("BOOBOT_CHANNELS", "#woob").split(",")
IRC_NICKNAME = os.getenv("BOOBOT_NICKNAME", "boobot")
IRC_SERVER = os.getenv("BOOBOT_SERVER", "dickson.freenode.net")
IRC_IGNORE = [re.compile(i) for i in os.getenv("BOOBOT_IGNORE", "!~?irker@").split(",")]
STORAGE_FILE = os.getenv("BOOBOT_STORAGE", "boobot.storage")


def fixurl(url):
    url = to_unicode(url)

    # remove javascript crap
    url = url.replace("/#!/", "/")

    # parse it
    parsed = urlparse.urlsplit(url)

    # divide the netloc further
    userpass, at, hostport = parsed.netloc.rpartition("@")
    user, colon1, pass_ = userpass.partition(":")
    host, colon2, port = hostport.partition(":")

    # encode each component
    scheme = parsed.scheme.encode("utf8")
    user = urllib.quote(user.encode("utf8"))
    colon1 = colon1.encode("utf8")
    pass_ = urllib.quote(pass_.encode("utf8"))
    at = at.encode("utf8")
    host = host.encode("idna")
    colon2 = colon2.encode("utf8")
    port = port.encode("utf8")
    path = "/".join(pce.encode("utf8") for pce in parsed.path.split("/"))
    # while valid, it is most likely an error
    path = path.replace("//", "/")
    query = parsed.query.encode("utf8")
    fragment = parsed.fragment.encode("utf8")

    # put it back together
    netloc = "".join((user, colon1, pass_, at, host, colon2, port))
    return urlparse.urlunsplit((scheme, netloc, path, query, fragment))


class BoobotBrowser(Browser):
    TIMEOUT = 3.0

    def urlinfo(self, url, maxback=2):
        if urlparse.urlsplit(url).netloc == "mobile.twitter.com":
            url = url.replace("mobile.twitter.com", "twitter.com", 1)
        try:
            r = self.open(url, method="HEAD")
            body = False
        except HTTPNotFound as e:
            if maxback and not url[-1].isalnum():
                return self.urlinfo(url[:-1], maxback - 1)
            raise e
        except BrowserHTTPError as e:
            if e.response.status_code in (501, 405):
                r = self.open(url)
                body = True
            else:
                raise e
        content_type = r.headers.get("Content-Type")
        try:
            size = int(r.headers.get("Content-Length"))
            hsize = self.human_size(size)
        except TypeError:
            size = None
            hsize = None
        is_html = ("html" in content_type) if content_type else re.match(r"\.x?html?$", url)
        title = None
        if is_html:
            if not body:
                r = self.open(url)
            # update size has we might not have it from headers
            size = len(r.content)
            hsize = self.human_size(size)

            page = HTMLPage(self, r)

            for title in page.doc.xpath("//head/title"):
                title = to_unicode(title.text_content()).strip()
                title = " ".join(title.split())
            if urlparse.urlsplit(url).netloc.endswith("twitter.com"):
                for title in page.doc.getroot().cssselect(".permalink-tweet .tweet-text"):
                    title = to_unicode(title.text_content()).strip()
                    title = " ".join(title.splitlines())

        return content_type, hsize, title

    def human_size(self, size):
        if size:
            units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")
            exponent = int(log(size, 1024))
            return f"{float(size) / pow(1024, exponent):.1f} {units[exponent]}"
        return "0 B"


class Task:
    def __init__(self, datetime, message, channel=None):
        self.datetime = datetime
        self.message = message
        self.channel = channel


class MyThread(Thread):
    daemon = True

    def __init__(self, bot):
        Thread.__init__(self)
        self.woob = Woob(storage=StandardStorage(STORAGE_FILE))
        self.woob.load_backends()
        self.bot = bot
        self.bot.set_woob(self.woob)

    def run(self):
        for ev in self.bot.joined.values():
            ev.wait()

        self.woob.repeat(5, self.check_tasks)
        self.woob.repeat(300, self.check_board)
        self.woob.repeat(600, self.check_dlfp)
        self.woob.repeat(600, self.check_twitter)

        self.woob.loop()

    def find_keywords(self, text):
        for word in ["woob", "weboob", "budget insight", "budget-insight", "budgetinsight", "budgea"]:
            if word in text.lower():
                return word
        return None

    def check_twitter(self):
        nb_tweets = 10

        for backend in self.woob.iter_backends(module="twitter"):
            for thread in list(itertools.islice(backend.iter_resources(None, ["search", "woob"]), 0, nb_tweets)):

                if not backend.storage.get("lastpurge"):
                    backend.storage.set("lastpurge", datetime.now() - timedelta(days=60))
                    backend.storage.save()

                if thread.id not in backend.storage.get("seen", default={}) and thread.date > backend.storage.get(
                    "lastpurge"
                ):
                    _item = thread.id.split("#")
                    url = f"https://twitter.com/{_item[0]}/status/{_item[1]}"
                    for msg in self.bot.on_url(url):
                        self.bot.send_message(f"{_item[0]}: {url}")
                        self.bot.send_message(msg)

                    backend.set_message_read(backend.fill_thread(thread, ["root"]).root)

    def check_dlfp(self):
        for msg in self.woob.do("iter_unread_messages", backends=["dlfp"]):
            word = self.find_keywords(msg.content)
            if word is not None:
                url = msg.signature[msg.signature.find("https://linuxfr") :]
                self.bot.send_message(f"[DLFP] {msg.sender} talks about {word}: {url}")
            self.woob[msg.backend].set_message_read(msg)

    def check_board(self):
        def iter_messages(backend):
            return backend.browser.iter_new_board_messages()

        for msg in self.woob.do(iter_messages, backends=["dlfp"]):
            word = self.find_keywords(msg.message)
            if word is not None and msg.login != "moules":
                message = msg.message.replace(word, "\002%s\002" % word)
                self.bot.send_message(f"[DLFP] <{msg.login}> {message}")

    def check_tasks(self):
        for task in list(self.bot.tasks_queue):
            if task.datetime < datetime.now():
                self.bot.send_message(task.message, task.channel)
                self.bot.tasks_queue.remove(task)

    def stop(self):
        self.woob.want_stop()
        self.woob.deinit()


class Boobot(SingleServerIRCBot):
    def __init__(self, channels, nickname, server, port=6667):
        SingleServerIRCBot.__init__(self, [(server, port)], nickname, nickname)
        # self.connection.add_global_handler('pubmsg', self.on_pubmsg)
        self.connection.add_global_handler("join", self.on_join)
        self.connection.add_global_handler("welcome", self.on_welcome)
        self.connection.buffer_class.errors = "replace"

        self.mainchannel = channels[0]
        self.joined = dict()
        for channel in channels:
            self.joined[channel] = Event()
        self.woob = None
        self.storage = None

        self.tasks_queue = []

    def set_woob(self, woob):
        self.woob = woob
        self.storage = ApplicationStorage("boobot", woob.storage)
        self.storage.load({})

    def on_welcome(self, c, event):
        for channel in self.joined.keys():
            c.join(channel)

    def on_join(self, c, event):
        # irclib 5.0 compatibility
        if callable(event.target):
            channel = event.target()
        else:
            channel = event.target
        self.joined[channel].set()

    def send_message(self, msg, channel=None):
        for m in msg.splitlines():
            msg = to_unicode(m).encode("utf-8")[:450].decode("utf-8")
            self.connection.privmsg(to_unicode(channel or self.mainchannel), msg)

    def on_pubmsg(self, c, event):
        # irclib 5.0 compatibility
        if callable(event.arguments):
            text = " ".join(event.arguments())
            channel = event.target()
            nick = event.source()
        else:
            text = " ".join(event.arguments)
            channel = event.target
            nick = event.source
        for ignore in IRC_IGNORE:
            if ignore.search(nick):
                return
        for m in re.findall(r"([\w\d_\-]+@\w+)", text):
            for msg in self.on_boobid(m):
                self.send_message(msg, channel)
        for m in re.findall(r"(https?://[^\s\xa0+]+)", text):
            for msg in self.on_url(m):
                self.send_message(msg, channel)

        m = re.match(r"^%(?P<cmd>\w+)(?P<args>.*)$", text)
        if m and hasattr(self, "cmd_%s" % m.groupdict()["cmd"]):
            getattr(self, "cmd_%s" % m.groupdict()["cmd"])(nick, channel, m.groupdict()["args"].strip())

    def cmd_at(self, nick, channel, text):
        try:
            datetime, message = text.split(" ", 1)
        except ValueError:
            self.send_message("Syntax: %at [YYYY-MM-DDT]HH:MM[:SS] message", channel)
            return

        try:
            datetime = parse_date(datetime)
        except ValueError:
            self.send_message("Unable to read date %r" % datetime)
            return

        self.tasks_queue.append(Task(datetime, message, channel))

    def cmd_addquote(self, nick, channel, text):
        quotes = self.storage.get(channel, "quotes", default=[])
        quotes.append({"author": nick, "timestamp": datetime.now(), "text": text})
        self.storage.set(channel, "quotes", quotes)
        self.storage.save()
        self.send_message("Quote #%s added" % (len(quotes) - 1), channel)

    def cmd_delquote(self, nick, channel, text):
        quotes = self.storage.get(channel, "quotes", default=[])

        try:
            n = int(text)
        except ValueError:
            self.send_message("Quote #%s not found gros" % text, channel)
            return

        quotes.pop(n)
        self.storage.set(channel, "quotes", quotes)
        self.storage.save()
        self.send_message("Quote #%s removed" % n, channel)

    def cmd_searchquote(self, nick, channel, text):
        try:
            pattern = re.compile(to_unicode(text), re.IGNORECASE | re.UNICODE)
        except Exception as e:
            self.send_message(str(e), channel)
            return

        quotes = []
        for quote in self.storage.get(channel, "quotes", default=[]):
            if pattern.search(to_unicode(quote["text"])):
                quotes.append(quote)

        try:
            quote = choice(quotes)  # nosec
        except IndexError:
            self.send_message("No match", channel)
        else:
            self.send_message("%s" % quote["text"], channel)

    def cmd_getquote(self, nick, channel, text):
        quotes = self.storage.get(channel, "quotes", default=[])
        if len(quotes) == 0:
            return

        try:
            n = int(text)
        except ValueError:
            n = randint(0, len(quotes) - 1)  # nosec

        try:
            quote = quotes[n]
        except IndexError:
            self.send_message("Unable to find quote #%s" % n, channel)
        else:
            self.send_message("[{}] {}".format(n, quote["text"]), channel)

    def on_boobid(self, boobid):
        _id, backend_name = boobid.split("@", 1)
        if backend_name in self.woob.backend_instances:
            backend = self.woob.backend_instances[backend_name]
            for cap in backend.iter_caps():
                func = "obj_info_%s" % cap.__name__[3:].lower()
                if hasattr(self, func):
                    try:
                        yield from getattr(self, func)(backend, _id)
                    except Exception as e:
                        print(get_backtrace())
                        yield f"Oops: [{type(e).__name__}] {e}"
                    break

    def on_url(self, url):
        url = fixurl(url)
        try:
            content_type, hsize, title = BoobotBrowser().urlinfo(url)
            if title:
                yield "URL: %s" % title
            elif hsize:
                yield f"URL (file): {content_type}, {hsize}"
            else:
                yield "URL (file): %s" % content_type
        except BrowserUnavailable as e:
            yield "URL (error): %s" % e
        except Exception as e:
            print(get_backtrace())
            yield f"Oops: [{type(e).__name__}] {e}"

    def obj_info_video(self, backend, id):
        v = backend.get_video(id)
        if v:
            yield f"Video: {v.title} ({v.duration})"

    def obj_info_housing(self, backend, id):
        h = backend.get_housing(id)
        if h:
            yield f"Housing: {h.title} ({h.area}m² / {h.cost}{h.currency})"


def main():
    logging.basicConfig(level=logging.DEBUG)
    bot = Boobot(IRC_CHANNELS, IRC_NICKNAME, IRC_SERVER)

    thread = MyThread(bot)
    thread.start()

    try:
        bot.start()
    except KeyboardInterrupt:
        print("Stopped.")

    thread.stop()


if __name__ == "__main__":
    sys.exit(main())
