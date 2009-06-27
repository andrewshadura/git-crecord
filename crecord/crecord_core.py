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
from mercurial import cmdutil, commands, extensions, hg, mdiff, patch
from mercurial import util
import cStringIO, errno, os, re, tempfile

from crpatch import parsepatch, filterpatch
from chunk_selector import chunkselector

def dorecord(ui, repo, committer, *pats, **opts):
    if not ui.interactive:
        raise util.Abort(_('running non-interactively, use commit instead'))

    def recordfunc(ui, repo, message, match, opts):
        """This is generic record driver.

        It's job is to interactively filter local changes, and accordingly
        prepare working dir into a state, where the job can be delegated to
        non-interactive commit command such as 'commit' or 'qrefresh'.

        After the actual job is done by non-interactive command, working dir
        state is restored to original.

        In the end we'll record intresting changes, and everything else will be
        left in place, so the user can continue his work.
        """
        if match.files():
            changes = None
        else:
            changes = repo.status(match=match)[:3]
            modified, added, removed = changes
            match = cmdutil.matchfiles(repo, modified + added + removed)
        diffopts = mdiff.diffopts(git=True, nodates=True)
        chunks = patch.diff(repo, repo.dirstate.parents()[0], match=match,
                            changes=changes, opts=diffopts)
        fp = cStringIO.StringIO()
        fp.write(''.join(chunks))
        fp.seek(0)

        # 1. filter patch, so we have intending-to apply subset of it
        if changes is not None:
            chunks = filterpatch(opts, parsepatch(changes, fp), chunkselector)
        else:
            chgs = repo.status(match=match)[:3]
            chunks = filterpatch(opts, parsepatch(chgs, fp), chunkselector)
            
        del fp

        contenders = {}
        for h in chunks:
            try: contenders.update(dict.fromkeys(h.files()))
            except AttributeError: pass

        newfiles = [f for f in match.files() if f in contenders]

        if not newfiles:
            ui.status(_('no changes to record\n'))
            return 0

        if changes is None:
            match = cmdutil.matchfiles(repo, newfiles)
            changes = repo.status(match=match)
        modified = dict.fromkeys(changes[0])

        # 2. backup changed files, so we can restore them in the end
        backups = {}
        backupdir = repo.join('record-backups')
        try:
            os.mkdir(backupdir)
        except OSError, err:
            if err.errno != errno.EEXIST:
                raise
        try:
            # backup continues
            for f in newfiles:
                if f not in modified:
                    continue
                fd, tmpname = tempfile.mkstemp(prefix=f.replace('/', '_')+'.',
                                               dir=backupdir)
                os.close(fd)
                ui.debug(_('backup %r as %r\n') % (f, tmpname))
                util.copyfile(repo.wjoin(f), tmpname)
                backups[f] = tmpname

            fp = cStringIO.StringIO()
            for c in chunks:
                if c.filename() in backups:
                    c.write(fp)
            dopatch = fp.tell()
            fp.seek(0)

            # 3a. apply filtered patch to clean repo  (clean)
            if backups:
                hg.revert(repo, repo.dirstate.parents()[0], backups.has_key)

            # 3b. (apply)
            if dopatch:
                try:
                    ui.debug(_('applying patch\n'))
                    ui.debug(fp.getvalue())
                    patch.internalpatch(fp, ui, 1, repo.root)
                except patch.PatchError, err:
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
            cwd = os.getcwd()
            os.chdir(repo.root)
            try:
                committer(ui, repo, newfiles, opts)
            finally:
                os.chdir(cwd)

            return 0
        finally:
            # 5. finally restore backed-up files
            try:
                for realname, tmpname in backups.iteritems():
                    ui.debug(_('restoring %r to %r\n') % (tmpname, realname))
                    util.copyfile(tmpname, repo.wjoin(realname))
                    os.unlink(tmpname)
                os.rmdir(backupdir)
            except OSError:
                pass
    return cmdutil.commit(ui, repo, recordfunc, pats, opts)


######  MAIN ENTRY POINTS FOR EXTENSION (crecord / qcrecord functions) ########

def crecord(ui, repo, *pats, **opts):
    '''interactively select changes to commit

    If a list of files is omitted, all changes reported by "hg status"
    will be candidates for recording.

    See 'hg help dates' for a list of formats valid for -d/--date.

    You will be shown a list of patch hunks from which you can select
    those you would like to apply to the commit.

    '''
    def record_committer(ui, repo, pats, opts):
        commands.commit(ui, repo, *pats, **opts)
    dorecord(ui, repo, record_committer, *pats, **opts)


def qcrecord(ui, repo, patch, *pats, **opts):
    '''interactively record a new patch

    see 'hg help qnew' & 'hg help record' for more information and usage
    '''

    try:
        mq = extensions.find('mq')
    except KeyError:
        raise util.Abort(_("'mq' extension not loaded"))

    def qrecord_committer(ui, repo, pats, opts):
        mq.new(ui, repo, patch, *pats, **opts)

    opts = opts.copy()
    opts['force'] = True    # always 'qnew -f'
    dorecord(ui, repo, qrecord_committer, *pats, **opts)
