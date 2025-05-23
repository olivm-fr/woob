# Copyright(C) 2009-2011  Romain Bignon, Christophe Benz
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

import logging
import re
import shlex
import subprocess
import time
from email import message_from_file
from email.header import Header, decode_header
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, parseaddr
from smtplib import SMTP

from woob.capabilities.messages import CapMessages, CapMessagesPost, Message, Thread
from woob.core import CallErrors
from woob.tools.application.repl import ReplApplication
from woob.tools.date import utc2local
from woob.tools.html import html2text
from woob.tools.misc import get_backtrace, to_unicode


__all__ = ["AppSmtp"]


class AppSmtp(ReplApplication):
    APPNAME = "smtp"
    OLD_APPNAME = "monboob"
    VERSION = "3.7"
    COPYRIGHT = "Copyright(C) 2010-YEAR Romain Bignon"
    DESCRIPTION = (
        "Daemon allowing to regularly check for new messages on various websites, "
        "and send an email for each message, and post a reply to a message on a website."
    )
    SHORT_DESCRIPTION = "daemon to send and check messages"
    CONFIG = {
        "interval": 300,
        "domain": "woob.example.org",
        "recipient": "woob@example.org",
        "smtp": "localhost",
        "pipe": "",
        "html": 0,
    }
    CAPS = CapMessages
    DISABLE_REPL = True

    def load_default_backends(self):
        self.load_backends(CapMessages, storage=self.create_storage())

    def main(self, argv):
        self.load_config()
        try:
            self.config.set("interval", int(self.config.get("interval")))
            if self.config.get("interval") < 1:
                raise ValueError()
        except ValueError:
            print("Configuration error: interval must be an integer >0.", file=self.stderr)
            return 1

        try:
            self.config.set("html", int(self.config.get("html")))
            if self.config.get("html") not in (0, 1):
                raise ValueError()
        except ValueError:
            print("Configuration error: html must be 0 or 1.", file=self.stderr)
            return 2

        return super().main(argv)

    def get_email_address_ident(self, msg, header):
        s = msg.get(header)
        if not s:
            return None
        m = re.match(".*<([^@]*)@(.*)>", s)
        if m:
            return m.group(1)
        else:
            try:
                return s.split("@")[0]
            except IndexError:
                return s

    def do_post(self, line):
        """
        post

        Pipe with a mail to post message.
        """
        msg = message_from_file(self.stdin)
        return self.process_incoming_mail(msg)

    def process_incoming_mail(self, msg):
        to = self.get_email_address_ident(msg, "To")
        sender = msg.get("From")
        reply_to = self.get_email_address_ident(msg, "In-Reply-To")

        title = msg.get("Subject")
        if title:
            new_title = ""
            for part in decode_header(title):
                if part[1]:
                    new_title += str(part[0], part[1])
                else:
                    new_title += str(part[0])
            title = new_title

        content = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                s = part.get_payload(decode=True)
                charsets = part.get_charsets() + msg.get_charsets()
                for charset in charsets:
                    try:
                        if charset is not None:
                            content += str(s, charset)
                        else:
                            content += str(s)
                    except UnicodeError as e:
                        self.logger.warning("Unicode error: %s" % e)
                        continue
                    except Exception as e:
                        self.logger.exception(e)
                        continue
                    else:
                        break

        if len(content) == 0:
            print("Unable to send an empty message", file=self.stderr)
            return 1

        # remove signature
        content = content.split("\n-- \n")[0]

        parent_id = None
        if reply_to is None:
            # This is a new message
            if "." in to:
                backend_name, thread_id = to.split(".", 1)
            else:
                backend_name = to
                thread_id = None
        else:
            # This is a reply
            try:
                backend_name, id = reply_to.split(".", 1)
                thread_id, parent_id = id.rsplit(".", 1)
            except ValueError:
                print("In-Reply-To header might be in form <backend.thread_id.message_id>", file=self.stderr)
                return 1

            # Default use the To header field to know the backend to use.
            if to and backend_name != to:
                backend_name = to

        try:
            backend = self.woob.backend_instances[backend_name]
        except KeyError:
            print("Backend %s not found" % backend_name, file=self.stderr)
            return 1

        if not backend.has_caps(CapMessagesPost):
            print("The backend %s does not implement CapMessagesPost" % backend_name, file=self.stderr)
            return 1

        thread = Thread(thread_id)
        message = Message(
            thread,
            0,
            title=title,
            sender=sender,
            receivers=[to],
            parent=Message(thread, parent_id) if parent_id else None,
            content=content,
        )
        try:
            backend.post_message(message)
        except Exception as e:
            content = "Unable to send message to %s:\n" % thread_id
            content += "\n\t%s\n" % to_unicode(e)
            if logging.root.level <= logging.DEBUG:
                content += "\n%s\n" % to_unicode(get_backtrace(e))
            self.send_email(
                backend.name,
                Message(
                    thread,
                    0,
                    title="Unable to send message",
                    sender="AppSmtp",
                    parent=Message(thread, parent_id) if parent_id else None,
                    content=content,
                ),
            )

    def do_run(self, line):
        """
        run

        Run the fetching daemon.
        """
        self.woob.repeat(self.config.get("interval"), self.process)
        self.woob.loop()

    def do_once(self, line):
        """
        once

        Send mails only once, then exit.
        """
        return self.process()

    def process(self):
        try:
            for message in self.woob.do("iter_unread_messages"):
                if self.send_email(message.backend, message):
                    self.woob[message.backend].set_message_read(message)
        except CallErrors as e:
            self.bcall_errors_handler(e)

    def send_email(self, backend_name, mail):
        domain = self.config.get("domain")
        recipient = self.config.get("recipient")

        parent_message = mail.parent
        references = []
        while parent_message:
            references.append(f"<{backend_name}.{mail.parent.full_id}@{domain}>")
            parent_message = parent_message.parent
        subject = mail.title
        sender = '"{}" <{}@{}>'.format(mail.sender.replace('"', '""') if mail.sender else "", backend_name, domain)

        # assume that .date is an UTC datetime
        date = formatdate(time.mktime(utc2local(mail.date).timetuple()), localtime=True)
        msg_id = f"<{backend_name}.{mail.full_id}@{domain}>"

        if self.config.get("html") and mail.flags & mail.IS_HTML:
            body = mail.content
            content_type = "html"
        else:
            if mail.flags & mail.IS_HTML:
                body = html2text(mail.content)
            else:
                body = mail.content
            content_type = "plain"

        if body is None:
            body = ""

        if mail.signature:
            if self.config.get("html") and mail.flags & mail.IS_HTML:
                body += "<p>-- <br />%s</p>" % mail.signature
            else:
                body += "\n\n-- \n"
                if mail.flags & mail.IS_HTML:
                    body += html2text(mail.signature)
                else:
                    body += mail.signature

        # Header class is smart enough to try US-ASCII, then the charset we
        # provide, then fall back to UTF-8.
        header_charset = "ISO-8859-1"

        # We must choose the body charset manually
        for body_charset in "US-ASCII", "ISO-8859-1", "UTF-8":
            try:
                body.encode(body_charset)
            except UnicodeError:
                pass
            else:
                break

        # Split real name (which is optional) and email address parts
        sender_name, sender_addr = parseaddr(sender)
        recipient_name, recipient_addr = parseaddr(recipient)

        # We must always pass Unicode strings to Header, otherwise it will
        # use RFC 2047 encoding even on plain ASCII strings.
        sender_name = str(Header(str(sender_name), header_charset))
        recipient_name = str(Header(str(recipient_name), header_charset))

        # Make sure email addresses do not contain non-ASCII characters
        sender_addr = sender_addr.encode("ascii")
        recipient_addr = recipient_addr.encode("ascii")

        # Create the message ('plain' stands for Content-Type: text/plain)
        msg = MIMEText(body.encode(body_charset), content_type, body_charset)
        msg["From"] = formataddr((sender_name, sender_addr))
        msg["To"] = formataddr((recipient_name, recipient_addr))
        msg["Subject"] = Header(str(subject), header_charset)
        msg["Message-Id"] = msg_id
        msg["Date"] = date
        if references:
            msg["In-Reply-To"] = references[0]
            msg["References"] = " ".join(reversed(references))

        self.logger.info(f"Send mail from <{sender}> to <{recipient}>")
        if len(self.config.get("pipe")) > 0:
            p = subprocess.Popen(
                shlex.split(self.config.get("pipe")),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            p.stdin.write(msg.as_string())
            p.stdin.close()
            if p.wait() != 0:
                self.logger.error("Unable to deliver mail: %s" % p.stdout.read().strip())
                return False
        else:
            # Send the message via SMTP to localhost:25
            try:
                smtp = SMTP(self.config.get("smtp"))
                smtp.sendmail(sender, recipient, msg.as_string())
            except Exception as e:
                self.logger.error("Unable to deliver mail: %s" % e)
                return False
            else:
                smtp.quit()

        return True
