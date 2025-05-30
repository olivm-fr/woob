#!/usr/bin/env python3

"""
This script edits your backends conf file by replacing stuff like:

[bnporc21]
_module = bnporc
website = pp
login = 123456
password = 78910

with:

[bnporc21]
_module = bnporc
website = pp
login = 123456
password = `pass show woob/bnporc21/password`
"""


import os
import re
import shutil
import subprocess
import sys
import tempfile


SECRET_KEYWORDS = ("password", "secret")

FILE = os.getenv("WOOB_BACKENDS") or os.path.expanduser("~/.config/woob/backends")

if not os.path.exists(FILE):
    print("the backends file does not exist")
    sys.exit(os.EX_NOINPUT)

if not shutil.which("pass"):
    print('the "pass" tool could not be found')
    sys.exit(os.EX_UNAVAILABLE)

errors = 0
seen = set()

backend = None

# building regex (^.*?keyword_1.*?|^.*?keyword_2.*?)\s*=\s*(\S.*)$
regex = r"(%s)\s*=\s*(\S.*)$" % "|".join(["^.*?" + keyword + ".*?" for keyword in SECRET_KEYWORDS])

with open(FILE) as inp:
    with tempfile.NamedTemporaryFile("w", delete=False, dir=os.path.dirname(FILE)) as outp:
        for line in inp:
            line = line.strip()

            mtc = re.match(regex, line)
            if mtc and not mtc.group(2).startswith("`"):
                cmd = ["pass", "insert", f"woob/{backend}/{mtc.group(1)}"]
                stdin = 2 * ("%s\n" % mtc.group(2))
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                proc.communicate(stdin.encode("utf-8"))
                if proc.returncode == 0:
                    print(f"{mtc.group(1)} = `pass show woob/{backend}/{mtc.group(1)}`", file=outp)
                    continue
                else:
                    errors += 1
                    print("warning: could not store password for backend %r" % backend)

            mtc = re.match(r"\[(.+)\]", line)
            if mtc:
                backend = mtc.group(1)
                if backend in seen:
                    print("error: backend %r is present multiple times" % backend)
                    sys.exit(os.EX_DATAERR)
                seen.add(backend)

            print(line, file=outp)

os.rename(outp.name, FILE)

if errors:
    print("%d errors were encountered when storing passwords securely" % errors)
    sys.exit(2)
