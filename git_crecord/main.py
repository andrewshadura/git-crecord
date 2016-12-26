from gettext import gettext as _
from .gitrepo import GitRepo
import os
import sys
from . import crecord_core
from . import util
import tempfile
import argparse

class Config:
    def get(self, section, item, default=None):
        try:
            return util.systemcall(['git', 'config', '--get', '%s.%s' % (section, item)], onerr=KeyError).rstrip('\n')
        except KeyError:
            return default

    def set(self, section, item, value, source=""):
        raise NotImplementedError

class Ui:
    def __init__(self, repo):
        self.repo = repo
        self.config = Config()
        self.debuglevel = 0
        try:
            self._username = "%s <%s>" % (self.config.get("user", "name"), self.config.get("user", "email"))
        except KeyError:
            self._username = None

    def debug(self, *msg, **opts):
        if self.debuglevel < 2:
            return
        for m in msg:
            sys.stdout.write(m)

    def info(self, *msg, **opts):
        if self.debuglevel < 1:
            return
        sys.stdout.flush()
        for m in msg:
            sys.stderr.write(m)
        sys.stderr.flush()

    def status(self, *msg, **opts):
        for m in msg:
            sys.stdout.write(m)

    def warn(self, *msg, **opts):
        sys.stdout.flush()
        for m in msg:
            sys.stderr.write(m)
        sys.stderr.flush()

    def setdebuglevel(self, level):
        self.debuglevel = level

    def setusername(self, username):
        self._username = username

    def username(self):
        if self._username is None:
            util.Abort(_("no name or email for the author was given"))
        return self._username

    def geteditor(self):
        editor = 'sensible-editor'
        return (os.environ.get("GIT_EDITOR") or
                self.config.get("core", "editor") or
                os.environ.get("VISUAL") or
                os.environ.get("EDITOR", editor))

    def edit(self, text, user, extra=None, name=None):
        fd = None
        if name is None:
            (fd, name) = tempfile.mkstemp(prefix='git-crecord-',
                                      suffix=".txt", text=True)
        try:
            if fd is not None:
                f = os.fdopen(fd, "w")
            else:
                f = open(name, "w")
            f.write(text)
            f.close()

            editor = self.geteditor()

            util.system("%s \"%s\"" % (editor, name),
                       onerr=util.Abort, errprefix=_("edit failed"))

            f = open(name)
            t = f.read()
            f.close()
        finally:
            if fd is not None:
                os.unlink(name)

        return t

    def commit(self, *files, **opts):
        (fd, name) = tempfile.mkstemp(prefix='git-crecord-',
                                      suffix=".txt", text=True)
        try:
            args = []

            # git-commit doesn't play nice with empty lines
            # and comments in the commit message when --edit
            # is used with --file;
            # to work that around, use --template when
            # no message is specified and --file otherwise.

            f = os.fdopen(fd, "w")
            if opts['message'] is not None:
                f.write(opts['message'])
            f.write(opts['template'])
            f.close()

            if opts['cleanup'] is None:
                opts['cleanup'] = 'strip'

            for k, v in opts.iteritems():
                if k in ('author', 'date', 'amend', 'signoff', 'cleanup'):
                    if v is None:
                        continue
                    if isinstance(v, bool):
                        if v is True:
                            args.append('--%s' % k)
                    else:
                        args.append('--%s=%s' % (k, v))

            util.system(['git', 'add', '-N', '--'] + list(files),
                       onerr=util.Abort, errprefix=_("add failed"))
            if opts['message'] is None:
                util.system(['git', 'commit', '--no-status', '-t', name] + args + ['--'] + list(files),
                           onerr=util.Abort, errprefix=_("commit failed"))
            else:
                util.system(['git', 'commit', '--no-status', '-F', name] + args + ['--'] + list(files),
                           onerr=util.Abort, errprefix=_("commit failed"))

        finally:
            os.unlink(name)


def main():
    prog = os.path.basename(sys.argv[0]).replace('-', ' ')

    subcommand = prog.split(' ')[-1].replace('.py', '')

    if subcommand == 'crecord':
        action = 'commit or stage'
    elif subcommand == 'cstage':
        action = 'stage'
    elif subcommand == 'cunstage':
        action = 'keep staged'

    parser = argparse.ArgumentParser(description='interactively select changes to %s' % action, prog=prog)
    parser.add_argument('--author', default=None, help='override author for commit')
    parser.add_argument('--date', default=None, help='override date for commit')
    parser.add_argument('-m', '--message', default=None, help='commit message')
    parser.add_argument('-s', '--signoff', action='store_true', default=False, help='add Signed-off-by:')
    parser.add_argument('--amend', action='store_true', default=False, help='amend previous commit')
    parser.add_argument('-v', '--verbose', default=0, action='count', help='be more verbose')
    parser.add_argument('--debug', action='store_const', const=2, dest='verbose', help='be debuggingly verbose')
    parser.add_argument('--cleanup', default=None, help=argparse.SUPPRESS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--cached', '--staged', action='store_true', default=False, help=argparse.SUPPRESS)
    group.add_argument('--index', action='store_true', default=False, help=argparse.SUPPRESS)
    args = parser.parse_args()

    opts = vars(args)

    if subcommand == 'cstage':
        opts['index'] = True

    if subcommand == 'cunstage':
        opts['cached'] = True

    repo = GitRepo(".")
    ui = Ui(repo)
    ui.setdebuglevel(opts['verbose'])

    os.chdir(repo.path)

    try:
        crecord_core.dorecord(ui, repo, None, **(opts))
    except util.Abort as inst:
        sys.stderr.write(_("abort: %s\n") % inst)
        sys.exit(1)
