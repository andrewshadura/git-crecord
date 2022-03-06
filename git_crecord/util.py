# util.py - utility functions from Mercurial
#
#  Copyright 2006, 2015 Matt Mackall <mpm@selenic.com>
#  Copyright 2007 Eric St-Jean <esj@wwd.ca>
#  Copyright 2009, 2011 Mads Kiilerich <mads@kiilerich.com>
#  Copyright 2015 Pierre-Yves David <pierre-yves.david@fb.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version

from __future__ import unicode_literals

from gettext import gettext as _
import os
import subprocess
import shutil
import sys
from typing import AnyStr, overload, Sequence, Optional

from . import encoding


closefds = os.name == 'posix'


def explainexit(code):
    """return a 2-tuple (desc, code) describing a subprocess status
    (codes from kill are negative - not os.system/wait encoding)"""
    if (code < 0) and (os.name == 'posix'):
        return _("killed by signal %d") % -code, -code
    else:
        return _("exited with status %d") % code, code


class Abort(Exception):
    pass


def system(cmd, cwd=None, onerr=None, errprefix=None):
    try:
        sys.stdout.flush()
    except Exception:
        pass

    if isinstance(cmd, list):
        shell = False
        prog = os.path.basename(cmd[0])
    else:
        shell = True
        prog = os.path.basename(cmd.split(None, 1)[0])

    rc = subprocess.call(cmd, shell=shell, close_fds=closefds,
                         cwd=cwd)
    if rc and onerr:
        errmsg = '%s %s' % (prog,
                            explainexit(rc)[0])
        if errprefix:
            errmsg = '%s: %s' % (errprefix, errmsg)
        raise onerr(errmsg)
    return rc


@overload
def systemcall(
        cmd: Sequence[AnyStr],
        encoding: str,
        dir: Optional[os.PathLike] = None,
        onerr=None,
        errprefix=None
) -> str:
    ...


@overload
def systemcall(
        cmd: Sequence[AnyStr],
        dir: Optional[os.PathLike] = None,
        onerr=None,
        errprefix=None
) -> bytes:
    ...


def systemcall(cmd, encoding=None, dir=None, onerr=None, errprefix=None):
    try:
        sys.stdout.flush()
    except Exception:
        pass

    p = subprocess.Popen(cmd, cwd=dir, stdout=subprocess.PIPE, close_fds=closefds)
    out = b''
    for line in iter(p.stdout.readline, b''):
        out = out + line
    p.wait()
    rc = p.returncode

    if rc and onerr:
        errmsg = '%s %s' % (os.path.basename(cmd[0]),
                            explainexit(rc)[0])
        if errprefix:
            errmsg = '%s: %s' % (errprefix, errmsg)
        raise onerr(errmsg)

    if encoding == "fs":
        return os.fsdecode(out)
    elif encoding:
        return out.decode(encoding)
    else:
        return out


def copyfile(src, dest, hardlink=False, copystat=False):
    '''copy a file, preserving mode and optionally other stat info like
    atime/mtime'''
    if os.path.lexists(dest):
        os.unlink(dest)
    # hardlinks are problematic on CIFS, quietly ignore this flag
    # until we find a way to work around it cleanly (issue4546)
    if False and hardlink:
        try:
            os.link(src, dest)
            return
        except (IOError, OSError):
            pass # fall back to normal copy
    if os.path.islink(src):
        os.symlink(os.readlink(src), dest)
        # copytime is ignored for symlinks, but in general copytime isn't needed
        # for them anyway
    else:
        try:
            shutil.copyfile(src, dest)
            if copystat:
                # copystat also copies mode
                shutil.copystat(src, dest)
            else:
                shutil.copymode(src, dest)
        except shutil.Error as inst:
            raise Abort(str(inst))


def ellipsis(text, maxlength=400):
    """Trim string to at most maxlength (default: 400) columns in display."""
    return encoding.trim(text, maxlength, ellipsis='...')


_notset = object()


def safehasattr(thing, attr):
    return getattr(thing, attr, _notset) is not _notset
