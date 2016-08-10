# crecord.py
#
# Copyright 2008 Mark Edgington <edgimar@gmail.com>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# Much of this extension is based on Bryan O'Sullivan's record extension.

'''text-gui based change selection during commit'''
from crecord_core import dorecord

def crecord(ui, repo, *pats, **opts):
    '''interactively select changes to commit

    If a list of files is omitted, all changes reported by :hg:`status`
    will be candidates for recording.

    See :hg:`help dates` for a list of formats valid for -d/--date.

    You will be shown a list of patch hunks from which you can select
    those you would like to apply to the commit.

    This command is not available when committing a merge.'''

    dorecord(ui, repo, None, *pats, **opts)

testedwith = '3.0.2 3.1.2 3.2.4 3.3.3 3.4.3 3.5.2 3.6'
buglink = 'https://bitbucket.org/edgimar/crecord/issues'
