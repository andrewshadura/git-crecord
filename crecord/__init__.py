# crecord.py
#
# Copyright 2008 Mark Edgington <edgimar@gmail.com>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# Much of this extension is based on Bryan O'Sullivan's record extension.

'''text-gui based change selection during commit or qrefresh'''
from mercurial.i18n import _
from mercurial import commands, extensions

from crecord_core import crecord, qcrecord

cmdtable = {
    "crecord":
        (crecord,

         # add commit options
         commands.table['^commit|ci'][1],

         _('hg crecord [OPTION]... [FILE]...')),
}


def extsetup():
    try:
        keyword = extensions.find('keyword')
        keyword.restricted += ' crecord qcrecord'
    except KeyError:
        pass

    try:
        mq = extensions.find('mq')
    except KeyError:
        return

    try:
        qcmdtable = {
        "qcrecord":
            (qcrecord,

             # add qnew options, except '--force'
             [opt for opt in mq.cmdtable['^qnew'][1] if opt[1] != 'force'],

             _('hg qcrecord [OPTION]... PATCH [FILE]...')),
        }
    except KeyError:
        # backwards compatible with pre 301633755dec
        qcmdtable = {
        "qcrecord":
            (qcrecord,
             [opt for opt in mq.cmdtable['qnew'][1] if opt[1] != 'force'],
             _('hg qcrecord [OPTION]... PATCH [FILE]...')),
        }

    cmdtable.update(qcmdtable)
