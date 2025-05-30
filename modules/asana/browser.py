# Copyright(C) 2017      Vincent Ardisson
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

import time

from dateutil.parser import parse as parse_date

from woob.browser.browsers import APIBrowser
from woob.browser.exceptions import ClientError
from woob.capabilities.base import NotAvailable
from woob.capabilities.bugtracker import Issue, Project, Status, Update, User
from woob.exceptions import BrowserIncorrectPassword


class AsanaBrowser(APIBrowser):
    BASEURL = "https://app.asana.com/api/1.0/"

    STATUS_OPEN = Status(0, "Open", Status.VALUE_NEW)
    STATUS_CLOSED = Status(1, "Closed", Status.VALUE_RESOLVED)

    def __init__(self, token, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = token
        self.session.headers["Authorization"] = "Bearer %s" % token

    def open(self, *args, **kwargs):
        try:
            return super().open(*args, **kwargs)
        except ClientError as e:
            if e.response.status_code == 401:
                raise BrowserIncorrectPassword()
            elif e.response.status_code == 429:
                self.logger.warning("reached requests quota...")
                waiting = int(e.response.headers["Retry-After"])
                if waiting <= 60:
                    self.logger.warning("waiting %s seconds", waiting)
                    time.sleep(waiting)
                    return super().open(*args, **kwargs)
                else:
                    self.logger.warning("not waiting %s seconds, just fuck it", waiting)

            raise

    def _make_user(self, data):
        u = User(data["gid"], None)
        if "name" in data:
            u.name = data["name"]
        return u

    def _make_project(self, data):
        p = Project(str(data["gid"]), data["name"])
        p.url = "https://app.asana.com/0/%s" % p.id
        if "members" in data:
            p.members = [self._make_user(u) for u in data["members"]]

        p.statuses = [self.STATUS_OPEN, self.STATUS_CLOSED]

        # these fields don't exist in asana
        p.priorities = []
        p.versions = []
        return p

    def _make_issue(self, data):
        if data["name"].endswith(":"):
            # section, not task
            return None

        i = Issue(str(data["gid"]))
        i.url = "https://app.asana.com/0/0/%s/f" % i.id
        i.title = data["name"]
        if "notes" in data:
            i.body = data["notes"]
        if data.get("assignee"):
            i.assignee = self._make_user(data["assignee"])
        if data.get("created_by"):
            # created_by is not documented
            i.author = self._make_user(data["created_by"])
        if "created_at" in data:
            i.creation = parse_date(data["created_at"])
        if "modified_at" in data:
            i.updated = parse_date(data["modified_at"])
        if "due_at" in data:
            if data["due_at"]:
                i.due = parse_date(data["due_at"])
            else:
                i.due = NotAvailable
        if "due_on" in data:
            if data["due_on"]:
                i.due = parse_date(data["due_on"])
            else:
                i.due = NotAvailable
        if data.get("projects"):
            i.project = self._make_project(data["projects"][0])
        if "completed" in data:
            i.status = self.STATUS_CLOSED if data["completed"] else self.STATUS_OPEN
        if "custom_fields" in data:

            def get(d):
                for k in ("string_value", "number_value", "enum_value", "text_value"):
                    if k in d:
                        return d[k]
                assert False, "custom type not handled"

            i.fields = {d["name"]: get(d) for d in data["custom_fields"]}
        if "tags" in data:
            i.tags = [d["name"] for d in data["tags"]]
        if data.get("memberships") and data["memberships"][0]["section"]:
            i.category = data["memberships"][0]["section"]["name"]

        i.version = NotAvailable
        i.priority = NotAvailable
        return i

    def _make_update(self, data):
        u = Update(str(data["id"]))
        if "created_at" in data:
            u.date = parse_date(data["created_at"])
        u.message = "{}: {}".format(data["type"], data["text"])
        if "created_by" in data:
            u.author = self._make_user(data["created_by"])
        return u

    def paginate(self, *args, **kwargs):
        params = kwargs.setdefault("params", {})
        params["limit"] = 20
        reply = self.request(*args, **kwargs)
        yield from reply["data"]
        while reply.get("next_page") and reply["next_page"].get("uri"):
            reply = self.request(reply["next_page"]["uri"])
            yield from reply["data"]
