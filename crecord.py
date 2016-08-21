#!/usr/bin/python2
from gettext import gettext as _
from dulwich.repo import Repo
import sys
import crecord
import crecord.util as util
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

parser = argparse.ArgumentParser(description='interactively select changes to commit')
parser.add_argument('--author', default=None, help='override author for commit')
parser.add_argument('--date', default=None, help='override date for commit')
parser.add_argument('-m', '--message', default='', help='commit message')
parser.add_argument('--amend', action='store_true', default=False, help='amend previous commit')
group = parser.add_mutually_exclusive_group()
group.add_argument('--cached', action='store_true', default=False, help='diff staging')
group.add_argument('--index', action='store_true', default=False, help='diff against index')
args = parser.parse_args()

repo = Repo(".")
ui = Ui(repo)
try:
    crecord.crecord(ui, repo, **(vars(args)))
except util.Abort as inst:
    sys.stderr.write(_("abort: %s\n") % inst)
    sys.exit(1)
