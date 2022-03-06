# encoding.py - character transcoding support for Mercurial
#
#  Copyright 2005-2009 Matt Mackall <mpm@selenic.com> and others
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

import unicodedata


# How to treat ambiguous-width characters. Set to 'WFA' to treat as wide.
wide = "WF"


def ucolwidth(d):
    """Find the column width of a Unicode string for display"""
    eaw = getattr(unicodedata, 'east_asian_width', None)
    if eaw is not None:
        return sum([eaw(c) in wide and 2 or 1 for c in d])
    return len(d)


