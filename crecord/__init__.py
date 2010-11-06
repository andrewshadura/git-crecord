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
from mercurial import commands, extensions, util

from crecord_core import dorecord

def crecord(ui, repo, *pats, **opts):
    '''interactively select changes to commit

    If a list of files is omitted, all changes reported by :hg:`status`
    will be candidates for recording.

    See :hg:`help dates` for a list of formats valid for -d/--date.

    You will be shown a list of patch hunks from which you can select
    those you would like to apply to the commit.

    This command is not available when committing a merge.'''

    dorecord(ui, repo, commands.commit, *pats, **opts)


def qcrecord(ui, repo, patch, *pats, **opts):
    '''interactively record a new patch

    See :hg:`help qnew` & :hg:`help crecord` for more information and
    usage.
    '''

    try:
        mq = extensions.find('mq')
    except KeyError:
        raise util.Abort(_("'mq' extension not loaded"))

    def committomq(ui, repo, *pats, **opts):
        mq.new(ui, repo, patch, *pats, **opts)

    opts = opts.copy()
    opts['force'] = True    # always 'qnew -f'
    dorecord(ui, repo, committomq, *pats, **opts)


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
        try:
            keyword.recordextensions += ' crecord'
            keyword.recordcommands += ' crecord qcrecord'
        except AttributeError:
            pass
    except KeyError:
        pass

    try:
        mq = extensions.find('mq')
    except KeyError:
        return

    qnew = '^qnew'
    if not qnew in mq.cmdtable:
        # backwards compatible with pre 301633755dec
        qnew = 'qnew'

    qcmdtable = {
    "qcrecord":
        (qcrecord,

         # add qnew options, except '--force'
         [opt for opt in mq.cmdtable[qnew][1] if opt[1] != 'force'],

         _('hg qcrecord [OPTION]... PATCH [FILE]...')),
    }

    cmdtable.update(qcmdtable)
