#!/bin/sh -u
. "$(dirname $0)/common.sh"

err=0

cd $(dirname $0)/..

MODULE_FILES=$(git ls-files modules|grep '\.py$')

# Takes PYFILES from env, if empty use all git tracked files
: ${PYFILES:=}
if [ -z "${PYFILES}" ]; then
  PYFILES="$(git ls-files | grep '^scripts\|\.py$'|grep -v '^modules'|grep -v '^contrib'|grep -v cookiecutter)"
  PYFILES="$PYFILES $MODULE_FILES"
fi

grep -n '[[:space:]]$' ${PYFILES} && echo 'Error: tabs or trailing whitespace found, remove them' && err=4
grep -Fn '.setlocale' ${PYFILES} && echo 'Error: do not use setlocale' && err=5
grep -Fn "os.isatty" ${PYFILES} && echo 'Error: Use stream.isatty() instead of os.isatty(stream.fileno())' && err=10
grep -Fn "raise StopIteration" ${PYFILES} && echo 'Error: PEP 479' && err=11

grep -nE "\.iter(keys|values|items)\(\)" ${PYFILES} | grep -Fv "six.iter" && echo 'Error: iterkeys/itervalues/iteritems is forbidden' && err=12

grep -nE "^ *print(\(| )" ${MODULE_FILES} && echo 'Error: Use of print in modules is forbidden, use logger instead' && err=20
grep -nE "HEADLESS[[:space:]]*=[[:space:]]*False" ${MODULE_FILES} && echo 'Error: HEADLESS must be set back to True' && err=23
grep -nE "^[ ]*from weboob" ${MODULE_FILES} && echo "Error: obsolete 'weboob' import (use 'woob' instead)" && err=24
grep -nE "^[ ]*import weboob" ${MODULE_FILES} && echo "Error: obsolete 'weboob' import (use 'woob' instead)" && err=24
grep -nE "^from modules.*" ${MODULE_FILES} && echo "Error: wrong 'from modules' import syntax" && err=25

# XXX this kind of warning may be replaced with DeprecationWarnings
grep -nE "^from woob.capabilities.wealth.*" ${MODULE_FILES} && echo "Error: obsolete 'woob.capabilities.bank.wealth' import" && err=26

if ${PYTHON} -c "import flake8" 2>/dev/null; then
    FLAKER=flake8
    OPT="--select=E9,F"
    if [ -n "${GITLAB_CI:=}" ]; then
        OPT="${OPT} --format=gl-codeclimate --output-file=gl-qa-report-flake8.json"
    fi
elif ${PYTHON} -c "import pyflakes" 2>/dev/null; then
    FLAKER=pyflakes
    OPT=
else
    echo "flake8 or pyflakes for python3 not found"
    err=1
fi

if [ ${err} -ne 1 ]; then
  $PYTHON -m ${FLAKER} ${OPT} ${PYFILES} || exit 33
fi

exit $err
