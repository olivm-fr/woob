# Copyright(C) 2010-2017  Vincent Ardisson
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

from functools import wraps


class SiteSwitch(Exception):
    """Exception to raise to switch to another Browser."""

    def __init__(self, name):
        """
        :param name: key of the `SwitchingBrowser.BROWSERS` dict to indicate
                     the new browser class to use
        :type name: str
        """
        super().__init__("Switching to site %s" % name)
        self.name = name


class SwitchingBrowser:
    """Proxy browser to use multiple (exclusive) browsers.

    When some sites have mutually exclusive sub-sites, it may be better to
    split a browser in multiple browsers. If it's not possible to know in
    advance what browser should be used, the SwitchingBrowser can help.

    Multiple browsers should be configured in the `BROWSERS` attribute as
    a dict. When first used, SwitchingBrowser will instanciate the browser
    class with the `'main'` key and proxy all method calls to it.
    If that browser raises :class:`SiteSwitch` exception, another browser
    (associated to the exception key parameter) will be instanciated and will
    be used to retry the call which failed.
    """

    BROWSERS = None

    """dict association keys to browser classes.

    It should contain a `'main'` key for the first browser class to use.
    """

    KEEP_SESSION = False

    """Whether to pass the :class:`requests.session.Session` between browsers.
    """

    KEEP_ATTRS = ()

    """Pass the values stored in __states__
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._browser_args = args
        self._browser_kwargs = kwargs
        self._browser = None

        self.set_browser("main")

    def set_browser(self, name):
        klass = self.BROWSERS[name]
        obj = klass(*self._browser_args, **self._browser_kwargs)
        if self._browser is not None:
            for attrname in self.KEEP_ATTRS:
                if hasattr(self._browser, attrname):
                    setattr(obj, attrname, getattr(self._browser, attrname))

            if self.KEEP_SESSION:
                obj.session = self._browser.session
            else:
                self._browser.session.close()

        self._browser = obj
        self._browser.logger.info("using %r browser", name)

    def __getattr__(self, attr):
        val = getattr(self._browser, attr)
        if not callable(val):
            return val

        @wraps(val)
        def wrapper(*args, **kwargs):
            try:
                return val(*args, **kwargs)
            except SiteSwitch as e:
                self.set_browser(e.name)
                val2 = getattr(self._browser, attr)
                return val2(*args, **kwargs)

        return wrapper


class SwitchingBrowserWithState(SwitchingBrowser):
    """Use state to transmit knowledge of last browser used during a previous sync to later start on the same browser"""

    def set_browser(self, name):
        super().set_browser(name)
        self.last_browser = name

    def load_state(self, state):
        """Get the last used browser from the state, if any"""

        self.set_browser(name=state.get("last_browser", "main"))
        self._browser.load_state(state)

    def dump_state(self):
        """Store the last used browser in the state"""

        ret = self._browser.dump_state()
        ret["last_browser"] = self.last_browser
        return ret
