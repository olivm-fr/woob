<%inherit file="layout.pyt"/>
from woob.tools.backend import Module${', BackendConfig' if r.login else ''}
% if login:
from woob.tools.value import Value, ValueBackendPassword
% endif
from ${r.capmodulename} import ${r.capname}

from .browser import ${r.classname}Browser


__all__ = ['${r.classname}Module']


class ${r.classname}Module(Module, ${r.capname}):
    NAME = '${r.name}'
    DESCRIPTION = '${r.description}'
    MAINTAINER = '${r.author}'
    EMAIL = '${r.email}'
    LICENSE = 'LGPLv3+'

    BROWSER = ${r.classname}Browser
% if login:

    CONFIG = BackendConfig(
        Value('username', help='Username'),
        ValueBackendPassword('password', help='Password'),
    )

    def create_default_browser(self):
        return self.create_browser(self.config['username'].get(), self.config['password'].get())
% endif

% for meth in r.methods:
${''.join(meth)}
% endfor
