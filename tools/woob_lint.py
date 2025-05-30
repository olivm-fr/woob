#!/usr/bin/env python3

import logging
import os
import sys

from woob.core import Woob


# Hint: use this script with file:///path/to/local/modules/ in sources.list
# if you want to correctly check all modules.


logging.basicConfig()

woob = Woob()
woob.modules_loader.load_all()

modules_without_tests = []
modules_without_icons = []


for name, module in woob.modules_loader.loaded.items():
    path = module.package.__path__[0]
    if not os.path.exists(os.path.join(path, "test.py")):
        modules_without_tests.append(name)
    if (
        not os.path.exists(os.path.join(path, "favicon.png"))
        and not os.path.exists(os.path.join(woob.repositories.icons_dir, "%s.png" % name))
        and not module.icon
    ):
        modules_without_icons.append(name)


if modules_without_tests:
    print("\nModules without tests: %s" % ", ".join(sorted(modules_without_tests)))
if modules_without_icons:
    print("\nModules without icons: %s" % ", ".join(sorted(modules_without_icons)))


if modules_without_tests or modules_without_icons:
    sys.exit(1)
