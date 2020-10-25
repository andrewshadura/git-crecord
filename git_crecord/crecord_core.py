# crecord.py
#
# Copyright 2008 Mark Edgington <edgimar@gmail.com>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# Much of this extension is based on Bryan O'Sullivan's record extension.

'''text-gui based change selection during commit or qrefresh'''
from gettext import gettext as _
from . import encoding
from . import util
import io
import errno
import os
import tempfile
import subprocess

from . import crpatch
from . import chunk_selector

def dorecord(ui, repo, commitfunc, *pats, **opts):
    def recordfunc(ui, repo, message, match, opts):
        """This is generic record driver.

        Its job is to interactively filter local changes, and accordingly
        prepare working dir into a state, where the job can be delegated to
        non-interactive commit command such as 'commit' or 'qrefresh'.

        After the actual job is done by non-interactive command, working dir
        state is restored to original.

        In the end we'll record interesting changes, and everything else will be
        left in place, so the user can continue his work.
        """

        git_args = ["git", "-c", "diff.mnemonicprefix=false", "diff", "--binary"]
        git_base = []

        if opts['cached']:
            git_args.append("--cached")

        if not opts['index'] and repo.head():
            git_base.append("HEAD")

        p = subprocess.Popen(git_args + git_base, stdout=subprocess.PIPE, close_fds=util.closefds)
        fp = p.stdout

        # 0. parse patch
        fromfiles = set()
        tofiles = set()

        chunks = crpatch.parsepatch(fp)
        for c in chunks:
            if isinstance(c, crpatch.uiheader):
                fromfile, tofile = c.files()
                if fromfile is not None:
                    fromfiles.add(fromfile)
                if tofile is not None:
                    tofiles.add(tofile)

        added = tofiles - fromfiles
        removed = fromfiles - tofiles
        modified = tofiles - added - removed
        changes = [modified, added, removed]

        # 1. filter patch, so we have intending-to apply subset of it
        chunks = crpatch.filterpatch(opts,
                                     chunks,
                                     chunk_selector.chunkselector, ui)
        p.wait()
        del fp

        contenders = set()
        for h in chunks:
            try:
                contenders.update(set(h.files()))
            except AttributeError:
                pass

        changed = changes[0] | changes[1] | changes[2]
        newfiles = [f for f in changed if f in contenders]

        if not newfiles:
            ui.status(_('no changes to record\n'))
            return 0


        # 2. backup changed files, so we can restore them in the end
        backups = {}
        newly_added_backups = {}
        backupdir = os.path.join(repo.controldir(), 'record-backups')
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
                fd, tmpname = tempfile.mkstemp(prefix=f.replace('/', '_')+'.',
                                               dir=backupdir)
                os.close(fd)
                ui.debug('backup %r as %r\n' % (f, tmpname))
                pathname = os.path.join(repo.path, f)
                if os.path.isfile(pathname):
                    util.copyfile(pathname, tmpname)
                if f in modified:
                    backups[f] = tmpname
                elif f in added:
                    newly_added_backups[f] = tmpname

            fp = io.StringIO()
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
                util.system(['git', 'checkout', '-f'] + git_base + ['--'] + [f for f in newfiles if f not in added],
                       onerr=util.Abort, errprefix=_("checkout failed"))
            # remove newly added files from 'clean' repo (so patch can apply)
            for f in newly_added_backups:
                pathname = os.path.join(repo.path, f)
                if os.path.isfile(pathname):
                    os.unlink(pathname)

            # 3b. (apply)
            if dopatch:
                try:
                    ui.debug('applying patch\n')
                    ui.debug(fp.getvalue())
                    p = subprocess.Popen(["git", "apply", "--whitespace=nowarn"], stdin=subprocess.PIPE, close_fds=util.closefds)
                    p.stdin.write(fp.read().encode(encoding.encoding))
                    p.stdin.close()
                    p.wait()
                except Exception as err:
                    s = str(err)
                    if s:
                        raise util.Abort(s)
                    else:
                        raise util.Abort(_('patch failed to apply'))
            del fp

            # 4. We prepared working directory according to filtered patch.
            #    Now is the time to delegate the job to commit/qrefresh or the like!

            # it is important to first chdir to repo root -- we'll call a
            # highlevel command with list of pathnames relative to repo root
            newfiles = [os.path.join(repo.path, n) for n in newfiles]
            if opts['operation'] == 'crecord':
                ui.commit(*newfiles, **opts)
            else:
                ui.stage(*newfiles, **opts)
            ui.debug('previous staging contents backed up as tree %r\n' % index_backup.indextree)
            index_backup = None

            return 0
        finally:
            # 5. finally restore backed-up files
            try:
                for realname, tmpname in backups.items():
                    ui.debug('restoring %r to %r\n' % (tmpname, realname))
                    util.copyfile(tmpname, os.path.join(repo.path, realname))
                    os.unlink(tmpname)
                for realname, tmpname in newly_added_backups.items():
                    ui.debug('restoring %r to %r\n' % (tmpname, realname))
                    util.copyfile(tmpname, os.path.join(repo.path, realname))
                    os.unlink(tmpname)
                os.rmdir(backupdir)
                if index_backup:
                    index_backup.write()
            except (OSError, NameError):
                pass

    return recordfunc(ui, repo, "", None, opts)
