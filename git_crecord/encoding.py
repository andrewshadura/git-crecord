# encoding.py - character transcoding support for Mercurial
#
#  Copyright 2005-2009 Matt Mackall <mpm@selenic.com> and others
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import absolute_import

import array
import locale
import os
import unicodedata
import sys

def pycompat():
    pass

pycompat.ispy3 = (sys.version_info[0] >= 3)

if pycompat.ispy3:
    import builtins

    def _sysstr(s):
        """Return a keyword str to be passed to Python functions such as
        getattr() and str.encode()

        This never raises UnicodeDecodeError. Non-ascii characters are
        considered invalid and mapped to arbitrary but unique code points
        such that 'sysstr(a) != sysstr(b)' for all 'a != b'.
        """
        if isinstance(s, builtins.str):
            return s
        return s.decode(u'latin-1')
else:
    def _sysstr(s):
        return s

if pycompat.ispy3:
    unichr = chr

# encoding.environ is provided read-only, which may not be used to modify
# the process environment
_nativeenviron = (not pycompat.ispy3 or os.supports_bytes_environ)
if not pycompat.ispy3:
    environ = os.environ
elif _nativeenviron:
    environ = os.environb
else:
    # preferred encoding isn't known yet; use utf-8 to avoid unicode error
    # and recreate it once encoding is settled
    environ = dict((k.encode(u'utf-8'), v.encode(u'utf-8'))
                   for k, v in os.environ.items())

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

if not _nativeenviron:
    # now encoding and helper functions are available, recreate the environ
    # dict to be exported to other modules
    environ = dict((tolocal(k.encode(u'utf-8')), tolocal(v.encode(u'utf-8')))
                   for k, v in os.environ.items())

# How to treat ambiguous-width characters. Set to 'WFA' to treat as wide.
wide = "WF"

def ucolwidth(d):
    "Find the column width of a Unicode string for display"
    eaw = getattr(unicodedata, 'east_asian_width', None)
    if eaw is not None:
        return sum([eaw(c) in wide and 2 or 1 for c in d])
    return len(d)

def getcols(s, start, c):
    '''Use colwidth to find a c-column substring of s starting at byte
    index start'''
    for x in range(start + c, len(s)):
        t = s[start:x]
        if colwidth(t) == c:
            return t

def trim(s, width, ellipsis=b'', leftside=False):
    """Trim string 's' to at most 'width' columns (including 'ellipsis').

    If 'leftside' is True, left side of string 's' is trimmed.
    'ellipsis' is always placed at trimmed side.

    >>> ellipsis = b'+++'
    >>> encoding = 'utf-8'
    >>> t = b'1234567890'
    >>> print(trim(t, 12, ellipsis=ellipsis))
    b'1234567890'
    >>> print(trim(t, 10, ellipsis=ellipsis))
    b'1234567890'
    >>> print(trim(t, 8, ellipsis=ellipsis))
    b'12345+++'
    >>> print(trim(t, 8, ellipsis=ellipsis, leftside=True))
    b'+++67890'
    >>> print(trim(t, 8))
    b'12345678'
    >>> print(trim(t, 8, leftside=True))
    b'34567890'
    >>> print(trim(t, 3, ellipsis=ellipsis))
    b'+++'
    >>> print(trim(t, 1, ellipsis=ellipsis))
    b'+'
    >>> u = u'\u3042\u3044\u3046\u3048\u304a' # 2 x 5 = 10 columns
    >>> t = u.encode('UTF-8')
    >>> print(trim(t, 12, ellipsis=ellipsis))
    b'\xe3\x81\x82\xe3\x81\x84\xe3\x81\x86\xe3\x81\x88\xe3\x81\x8a'
    >>> print(trim(t, 10, ellipsis=ellipsis))
    b'\xe3\x81\x82\xe3\x81\x84\xe3\x81\x86\xe3\x81\x88\xe3\x81\x8a'
    >>> print(trim(t, 8, ellipsis=ellipsis))
    b'\xe3\x81\x82\xe3\x81\x84+++'
    >>> print(trim(t, 8, ellipsis=ellipsis, leftside=True))
    b'+++\xe3\x81\x88\xe3\x81\x8a'
    >>> print(trim(t, 5))
    b'\xe3\x81\x82\xe3\x81\x84'
    >>> print(trim(t, 5, leftside=True))
    b'\xe3\x81\x88\xe3\x81\x8a'
    >>> print(trim(t, 4, ellipsis=ellipsis))
    b'+++'
    >>> print(trim(t, 4, ellipsis=ellipsis, leftside=True))
    b'+++'
    """
    try:
        u = s.decode(_sysstr(encoding))
    except UnicodeDecodeError:
        if len(s) <= width: # trimming is not needed
            return s
        width -= len(ellipsis)
        if width <= 0: # no enough room even for ellipsis
            return ellipsis[:width + len(ellipsis)]
        if leftside:
            return ellipsis + s[-width:]
        return s[:width] + ellipsis

    if ucolwidth(u) <= width: # trimming is not needed
        return s

    width -= len(ellipsis)
    if width <= 0: # no enough room even for ellipsis
        return ellipsis[:width + len(ellipsis)]

    if leftside:
        uslice = lambda i: u[i:]
        concat = lambda s: ellipsis + s
    else:
        uslice = lambda i: u[:-i]
        concat = lambda s: s + ellipsis
    for i in range(1, len(u)):
        usub = uslice(i)
        if ucolwidth(usub) <= width:
            return concat(usub.encode(_sysstr(encoding)))
    return ellipsis # no enough room for multi-column characters

