#!/usr/bin/python

import sys

import resources.lib.constants as constants
import resources.lib.test.common_test as common_xbmc
from resources.lib.actions import actions


print(sys.argv)
if len(sys.argv) < 2:
    actions[constants.DISPLAY_MENU]()._do()
else:
    params = common_xbmc.parse_params(sys.argv[1])
    # print params
    action = params.get("action")
    if action:
        actions[action]()._do(params)
    else:
        common_xbmc.display_error(" ARGV Nothing done.. verify params " + repr(params))
