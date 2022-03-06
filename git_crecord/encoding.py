# Unicode string width calculator
#
#  Copyright 2009, 2010 Matt Mackall <mpm@selenic.com>
#  Copyright 2010, 2011 FUJIWARA Katsunori <foozy@lares.dti.ne.jp>
#  Copyright 2011 Augie Fackler <durin42@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.
#
# SPDX-License-Identifier: GPL-2.0-or-later

import unicodedata


# How to treat ambiguous-width characters. Set to 'WFA' to treat as wide.
wide = "WF"


def ucolwidth(d: str) -> int:
    """Find the column width of a Unicode string for display"""
    eaw = getattr(unicodedata, 'east_asian_width', None)
    if eaw is not None:
        return sum([eaw(c) in wide and 2 or 1 for c in d])
    return len(d)


