# Record process driver
#
# Copyright 2008—2011, 2014 Mark Edgington <edgimar@gmail.com>
# Copyright 2016, 2018—2022 Andrej Shadura <andrew@shadura.me>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# Much of this extension is based on Bryan O'Sullivan's record extension.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from gettext import gettext as _

import io
import errno
import os
import tempfile
import subprocess
from typing import IO, cast

from .crpatch import Header, parsepatch, filterpatch
from .chunk_selector import chunkselector
from .gitrepo import GitRepo
from .util import Abort, system, closefds, copyfile


def dorecord(ui, repo, *pats, **opts):
    """This is generic record driver.

    Its job is to interactively filter local changes, and accordingly
    prepare working dir into a state, where the job can be delegated to
    non-interactive commit command such as 'commit' or 'qrefresh'.

    After the actual job is done by non-interactive command, working dir
    state is restored to original.

    In the end we'll record interesting changes, and everything else will be
    left in place, so the user can continue his work.

    We pass additional configuration options to Git to make the diff output
    consistent:
     - core.quotePath:
       Limit symbols requiring escaping to double-quotes, backslash and
       control characters.
     - diff.mnemonicPrefix:
       If set, git diff uses a prefix pair that is different from the standard
       "a/" and "b/" depending on what is being compared. Our parser only
       supports "a/" and "b/".
    """

    git_args = ["git", "-c", "core.quotePath=false", "-c", "diff.mnemonicPrefix=false", "diff", "--binary"]
    git_base = []

    if opts['cached']:
        git_args.append("--cached")

    if not opts['index'] and repo.head():
        git_base.append("HEAD")

    p = subprocess.Popen(git_args + git_base, stdout=subprocess.PIPE, close_fds=closefds)
    fp = cast(IO[bytes], p.stdout)

    # 0. parse patch
    fromfiles = set()
    tofiles = set()

    chunks = parsepatch(fp)
    for c in chunks:
        if isinstance(c, Header):
            fromfile, tofile = c.files()
            if fromfile is not None:
                fromfiles.add(os.fsdecode(fromfile))
            if tofile is not None:
                tofiles.add(os.fsdecode(tofile))

    added = tofiles - fromfiles
    removed = fromfiles - tofiles
    modified = tofiles - added - removed
    changes = [modified, added, removed]

    # 1. filter patch, so we have intending-to apply subset of it
    chunks = filterpatch(opts,
                         chunks,
                         chunkselector, ui)
    p.wait()
    del fp

    contenders = set()
    for h in chunks:
        fromfile, tofile = h.files()
        if fromfile is not None:
            contenders.add(os.fsdecode(fromfile))
        if tofile is not None:
            contenders.add(os.fsdecode(tofile))

    changed = changes[0] | changes[1] | changes[2]
    newfiles: list = [f for f in changed if f in contenders]

    if not newfiles:
        ui.status(_('no changes to record'))
        return 0

    # 2. backup changed files, so we can restore them in the end
    backups = {}
    newly_added_backups = {}
    backupdir = repo.controldir / 'record-backups'
    try:
        os.mkdir(backupdir)
    except OSError as err:
        if err.errno != errno.EEXIST:
            raise
    index_backup = None
    try:
        index_backup = repo.open_index()
        index_backup.backup_tree()

        # backup continues
        for f in newfiles:
            if f not in (modified | added):
                continue
            prefix = os.fsdecode(f).replace('/', '_') + '.'
            fd, tmpname = tempfile.mkstemp(prefix=prefix,
                                           dir=backupdir)
            os.close(fd)
            ui.debug('backup %r as %r' % (f, tmpname))
            pathname = repo.path / f
            if os.path.isfile(pathname):
                copyfile(pathname, tmpname)
            if f in modified:
                backups[f] = tmpname
            elif f in added:
                newly_added_backups[f] = tmpname

        fp = io.BytesIO()
        all_backups = {}
        all_backups.update(backups)
        all_backups.update(newly_added_backups)
        for c in chunks:
            c.write(fp)
        dopatch = fp.tell()
        fp.seek(0)

        # 2.5 optionally review / modify patch in text editor
        if opts['crecord_reviewpatch']:
            patchtext = fp.read()
            reviewedpatch = ui.edit(patchtext, "")
            fp.truncate(0)
            fp.write(reviewedpatch)
            fp.seek(0)

        # 3a. apply filtered patch to clean repo  (clean)
        if backups or any((f in contenders for f in removed)):
            system(['git', 'checkout', '-f'] + git_base + ['--'] + [f for f in newfiles if f not in added],
                   onerr=Abort, errprefix=_("checkout failed"))
        # remove newly added files from 'clean' repo (so patch can apply)
        for f in newly_added_backups:
            pathname = repo.path / f
            pathname.unlink(missing_ok=True)

        # 3b. (apply)
        if dopatch:
            try:
                ui.debug('applying patch')
                ui.debug(fp.getvalue().decode("UTF-8", "hexreplace"))
                p = subprocess.Popen(
                    ["git", "apply", "--whitespace=nowarn"],
                    stdin=subprocess.PIPE,
                    close_fds=closefds
                )
                p.stdin.write(fp.getvalue())
                p.stdin.close()
                p.wait()
            except Exception as err:
                s = str(err)
                if s:
                    raise Abort(s)
                else:
                    raise Abort(_('patch failed to apply'))
        del fp

        # 4. We prepared working directory according to filtered patch.
        #    Now is the time to delegate the job to commit/qrefresh or the like!

        # it is important to first chdir to repo root -- we'll call a
        # highlevel command with list of pathnames relative to repo root
        newfiles = [repo.path / n for n in newfiles]
        if opts['operation'] == 'crecord':
            ui.commit(*newfiles, **opts)
        else:
            ui.stage(*newfiles, **opts)
        ui.debug('previous staging contents backed up as tree %r' % index_backup.indextree)
        index_backup = None

        return 0
    finally:
        # 5. finally restore backed-up files
        try:
            for realname, tmpname in backups.items():
                ui.debug('restoring %r to %r' % (tmpname, realname))
                copyfile(tmpname, os.path.join(repo.path, realname))
                os.unlink(tmpname)
            for realname, tmpname in newly_added_backups.items():
                ui.debug('restoring %r to %r' % (tmpname, realname))
                copyfile(tmpname, os.path.join(repo.path, realname))
                os.unlink(tmpname)
            os.rmdir(backupdir)
            if index_backup:
                index_backup.write()
        except (OSError, NameError):
            pass
