# Utility functions
#
#  Copyright 2006, 2015 Matt Mackall <mpm@selenic.com>
#  Copyright 2007 Eric St-Jean <esj@wwd.ca>
#  Copyright 2009, 2011 Mads Kiilerich <mads@kiilerich.com>
#  Copyright 2015 Pierre-Yves David <pierre-yves.david@fb.com>
#  Copyright 2016, 2022 Andrej Shadura <andrew@shadura.me>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version
#
# Some of these utilities were originally taken from Mercurial.
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

from gettext import gettext as _
import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import AnyStr, overload, Sequence, Optional, Union

from .encoding import ucolwidth


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
        cmd: Sequence[str] | Sequence[bytes],
        encoding: str,
        dir: Optional[os.PathLike | str] = None,
        onerr=None,
        errprefix=None
) -> str:
    ...


@overload
def systemcall(
        cmd: Sequence[str] | Sequence[bytes],
        dir: Optional[os.PathLike | str] = None,
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


def copyfile(src: Union[str, Path], dest: Union[str, Path], copystat=True):
    """Copy a file, preserving mode and optionally other stat info like atime/mtime"""
    if os.path.lexists(dest):
        os.unlink(dest)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dest)
        # copytime is ignored for symlinks, but in general copytime isn't needed
        # for them anyway
    else:
        try:
            shutil.copyfile(src, dest)  # type: ignore
            if copystat:
                # copystat also copies mode
                shutil.copystat(src, dest)
            else:
                shutil.copymode(src, dest)
        except shutil.Error as inst:
            raise Abort(str(inst))


def ellipsis(text, maxlength=400):
    """Trim string to at most maxlength (default: 400) columns in display."""
    return trim(text, maxlength, ellipsis='...')


def trim(s, width, ellipsis='', leftside=False):
    """Trim string 's' to at most 'width' columns (including 'ellipsis').

    If 'leftside' is True, left side of string 's' is trimmed.
    'ellipsis' is always placed at trimmed side.

    >>> ellipsis = '+++'
    >>> encoding = 'utf-8'
    >>> t = '1234567890'
    >>> print(trim(t, 12, ellipsis=ellipsis))
    1234567890
    >>> print(trim(t, 10, ellipsis=ellipsis))
    1234567890
    >>> print(trim(t, 8, ellipsis=ellipsis))
    12345+++
    >>> print(trim(t, 8, ellipsis=ellipsis, leftside=True))
    +++67890
    >>> print(trim(t, 8))
    12345678
    >>> print(trim(t, 8, leftside=True))
    34567890
    >>> print(trim(t, 3, ellipsis=ellipsis))
    +++
    >>> print(trim(t, 1, ellipsis=ellipsis))
    +
    >>> t = '\u3042\u3044\u3046\u3048\u304a' # 2 x 5 = 10 columns
    >>> print(trim(t, 12, ellipsis=ellipsis))
    \u3042\u3044\u3046\u3048\u304a
    >>> print(trim(t, 10, ellipsis=ellipsis))
    \u3042\u3044\u3046\u3048\u304a
    >>> print(trim(t, 8, ellipsis=ellipsis))
    \u3042\u3044+++
    >>> print(trim(t, 8, ellipsis=ellipsis, leftside=True))
    +++\u3048\u304a
    >>> print(trim(t, 5))
    \u3042\u3044
    >>> print(trim(t, 5, leftside=True))
    \u3048\u304a
    >>> print(trim(t, 4, ellipsis=ellipsis))
    +++
    >>> print(trim(t, 4, ellipsis=ellipsis, leftside=True))
    +++
    """
    if ucolwidth(s) <= width:  # trimming is not needed
        return s

    width -= len(ellipsis)
    if width <= 0:  # no enough room even for ellipsis
        return ellipsis[:width + len(ellipsis)]

    if leftside:
        uslice = lambda i: s[i:]
        concat = lambda s: ellipsis + s
    else:
        uslice = lambda i: s[:-i]
        concat = lambda s: s + ellipsis
    for i in range(1, len(s)):
        usub = uslice(i)
        if ucolwidth(usub) <= width:
            return concat(usub)
    return ellipsis  # no enough room for multi-column characters


_notset = object()


def safehasattr(thing, attr):
    return getattr(thing, attr, _notset) is not _notset


def unescape_filename(filename: bytes) -> bytes:
    r"""Unescape a filename after Git mangled it for "git diff --git" line.

    >>> unescape_filename(b'a/\\321\\216\\321\\217')
    b'a/\xd1\x8e\xd1\x8f'
    >>> unescape_filename(b'a/\\\\')
    b'a/\\'
    >>> unescape_filename(b'a/file\\55name')
    b'a/file-name'
    """
    unescaped_unicode = filename.decode('unicode_escape')
    return bytes(ord(x) for x in unescaped_unicode)


def unwrap_filename(filename: bytes) -> bytes:
    r"""Unwrap a filename mangled by Git

    If the filename is in double quotes, remove them and unescape enclosed characters.
    Otherwise, return the input as is.

    >>> def apply(f, s: str) -> str:
    ...     return f(s.encode("UTF-8")).decode("UTF-8")
    >>> apply(unwrap_filename, 'a/filename')
    'a/filename'
    >>> apply(unwrap_filename, 'a/имя-файла')
    'a/имя-файла'
    >>> apply(unwrap_filename, '"a/file\\55name"')
    'a/file-name'
    >>> apply(unwrap_filename, '"a/им\\321\\217\55\\\\name"')
    'a/имя-\\name'
    """
    if filename.startswith(b'"') and filename.endswith(b'"'):
        return unescape_filename(filename[1:-1])
    else:
        return filename
