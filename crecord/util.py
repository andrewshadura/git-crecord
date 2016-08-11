# util.py - utility functions from Mercurial
#
#  Copyright 2006, 2015 Matt Mackall <mpm@selenic.com>
#  Copyright 2007 Eric St-Jean <esj@wwd.ca>
#  Copyright 2015 Pierre-Yves David <pierre-yves.david@fb.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version

import os
import shutil

class Abort(Exception):
    pass

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
