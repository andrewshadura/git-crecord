# encoding.py - character transcoding support for Mercurial
#
#  Copyright 2005-2009 Matt Mackall <mpm@selenic.com> and others
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import unicode_literals, absolute_import, print_function

import array
import locale
import os
import unicodedata
import sys


def _getpreferredencoding():
    '''
    On darwin, getpreferredencoding ignores the locale environment and
    always returns mac-roman. http://bugs.python.org/issue6202 fixes this
    for Python 2.7 and up. This is the same corrected code for earlier
    Python versions.

    However, we can't use a version check for this method, as some distributions
    patch Python to fix this. Instead, we use it as a 'fixer' for the mac-roman
    encoding, as it is unlikely that this encoding is the actually expected.
    '''
    try:
        locale.CODESET
    except AttributeError:
        # Fall back to parsing environment variables :-(
        return locale.getdefaultlocale()[1]

    oldloc = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "")
    result = locale.nl_langinfo(locale.CODESET)
    locale.setlocale(locale.LC_CTYPE, oldloc)

    return result


_encodingfixers = {
    '646': lambda: 'ascii',
    'ANSI_X3.4-1968': lambda: 'ascii',
    'mac-roman': _getpreferredencoding
}

try:
    encoding = locale.getpreferredencoding() or 'ascii'
    encoding = _encodingfixers.get(encoding, lambda: encoding)()
except locale.Error:
    encoding = 'ascii'
encodingmode = "strict"
fallbackencoding = 'ISO-8859-1'

# How to treat ambiguous-width characters. Set to 'WFA' to treat as wide.
wide = "WF"


def ucolwidth(d):
    """Find the column width of a Unicode string for display"""
    eaw = getattr(unicodedata, 'east_asian_width', None)
    if eaw is not None:
        return sum([eaw(c) in wide and 2 or 1 for c in d])
    return len(d)


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
