#!/usr/bin/python2
from gettext import gettext as _
from dulwich.repo import Repo
import os
import sys
import crecord
import crecord.util as util
import tempfile
import argparse

class configproxy:
    def __init__(self, config):
        self._config = config

    def get(self, section, item, default=None):
        try:
            return self._config.get(section, item)
        except KeyError:
            return default

    def set(self, section, item, value, source=""):
        return self._config.set(section, item, value)

class Ui:
    def __init__(self, repo):
        self.repo = repo
        self.config = configproxy(repo.get_config_stack())
        try:
            self._username = "%s <%s>" % (self.config.get("user", "name"), self.config.get("user", "email"))
        except KeyError:
            self._username = None

    def debug(self, *msg, **opts):
        for m in msg:
            sys.stdout.write(m)

    def status(self, *msg, **opts):
        for m in msg:
            sys.stdout.write(m)

    def warn(self, *msg, **opts):
        sys.stdout.flush()
        for m in msg:
            sys.stderr.write(m)
        sys.stderr.flush()

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

    def edit(self, text, user, extra=None, editform=None, pending=None):
        (fd, name) = tempfile.mkstemp(prefix='git-crecord-',
                                      suffix=".txt", text=True)
        try:
            f = os.fdopen(fd, "w")
            f.write(text)
            f.close()

            editor = self.geteditor()

            util.system("%s \"%s\"" % (editor, name),
                       onerr=util.Abort, errprefix=_("edit failed"))

            f = open(name)
            t = f.read()
            f.close()
        finally:
            os.unlink(name)

        return t

    def commit(self, *files, **opts):
        (fd, name) = tempfile.mkstemp(prefix='git-crecord-',
                                      suffix=".txt", text=True)
        try:
            f = os.fdopen(fd, "w")
            f.write(opts['message'])
            f.close()

            args = []
            for k, v in opts.iteritems():
                if k in ('author', 'date', 'amend'):
                    if v is None:
                        continue
                    if isinstance(v, bool):
                        if v is True:
                            args.append('--%s' % k)
                    else:
                        args.append('--%s=%s' % (k, v))

            util.system(['git', 'commit', '-F', name] + args + ['--'] + list(files),
                       onerr=util.Abort, errprefix=_("commit failed"))

        finally:
            os.unlink(name)

prog = os.path.basename(sys.argv[0]).replace('-', ' ')

parser = argparse.ArgumentParser(description='interactively select changes to commit', prog=prog)
parser.add_argument('--author', default=None, help='override author for commit')
parser.add_argument('--date', default=None, help='override date for commit')
parser.add_argument('-m', '--message', default='', help='commit message')
parser.add_argument('--amend', action='store_true', default=False, help='amend previous commit')
group = parser.add_mutually_exclusive_group()
group.add_argument('--cached', '--staged', action='store_true', default=False, help=argparse.SUPPRESS)
group.add_argument('--index', action='store_true', default=False, help=argparse.SUPPRESS)
args = parser.parse_args()

repo = Repo(".")
ui = Ui(repo)
try:
    crecord.crecord(ui, repo, **(vars(args)))
except util.Abort as inst:
    sys.stderr.write(_("abort: %s\n") % inst)
    sys.exit(1)
