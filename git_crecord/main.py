# git-crecord entry point and configuration helpers
#
# Copyright 2016, 2018—2022 Andrej Shadura <andrew@shadura.me>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# SPDX-License-Identifier: GPL-2.0-or-later

from gettext import gettext as _
from pathlib import Path
from typing import Optional

import argparse
import os
import sys
import tempfile

from . import crecord_core
from .gitrepo import GitRepo
from .util import Abort, system, systemcall


class Config:
    def get(self, section, item, default=None) -> Optional[str]:
        try:
            return systemcall(
                ['git', 'config', '--get', '%s.%s' % (section, item)],
                onerr=KeyError,
                encoding="UTF-8",
            ).rstrip('\n')
        except KeyError:
            return default

    def set(self, section, item, value, source=""):
        raise NotImplementedError


class Ui:
    def __init__(self, repo: GitRepo):
        self.repo = repo
        self.config = Config()
        self.debuglevel = 0

    def print_message(self, *msg, debuglevel: int, **opts):
        if self.debuglevel < debuglevel:
            return

        sys.stdout.flush()
        print(*msg, **opts, file=sys.stderr)
        sys.stderr.flush()

    def debug(self, *msg, **opts):
        self.print_message(*msg, debuglevel=2, **opts)

    def info(self, *msg, **opts):
        self.print_message(*msg, debuglevel=1, **opts)

    def warn(self, *msg, **opts):
        self.print_message(*msg, debuglevel=0, **opts)

    def status(self, *msg, **opts):
        print(*msg, **opts)

    def setdebuglevel(self, level):
        self.debuglevel = level

    @property
    def editor(self) -> str:
        return (os.environ.get("GIT_EDITOR") or
                self.config.get("core", "editor") or
                os.environ.get("VISUAL") or
                os.environ.get("EDITOR") or
                'sensible-editor')

    def edit(self, text: bytes, user, extra=None, name=None) -> bytes:
        f = tempfile.NamedTemporaryFile(
            prefix='git-crecord-',
            suffix=".txt",
            mode="wb",
            delete=False,
        )
        try:
            f.write(text)
            f.close()

            editor = self.editor

            system("%s \"%s\"" % (editor, f.name),
                   onerr=Abort, errprefix=_("edit failed"))

            t = Path(f.name).read_bytes()

        finally:
            os.unlink(f.name)

        return t

    def stage(self, *files, **opts):
        to_add = [f for f in files if os.path.exists(f)]
        if to_add:
            system(['git', 'add', '-f', '-N', '--'] + to_add,
                   onerr=Abort, errprefix=_("add failed"))

    def commit(self, *files, **opts):
        msgfile = self.repo.controldir / "CRECORD_COMMITMSG"
        try:
            args = []

            if opts['message']:
                msgfile.write_text(opts['message'])

            if opts['cleanup'] is None:
                opts['cleanup'] = 'strip'

            for k, v in opts.items():
                if k in ('author', 'date', 'amend', 'signoff', 'cleanup',
                         'reset_author', 'gpg_sign', 'no_gpg_sign',
                         'reedit_message', 'reuse_message', 'fixup', 'quiet'):
                    if v is None:
                        continue
                    if isinstance(v, bool):
                        if v is True:
                            args.append('--%s' % k.replace('_', '-'))
                    else:
                        args.append('--%s=%s' % (k.replace('_', '-'), v))

            to_add = [f for f in files if os.path.exists(f)]
            if to_add:
                system(['git', 'add', '-f', '-N', '--'] + to_add,
                       onerr=Abort, errprefix=_("add failed"))
            if not opts['message']:
                system(['git', 'commit'] + args + ['--'] + list(files),
                       onerr=Abort, errprefix=_("commit failed"))
            else:
                system(['git', 'commit', '-F', msgfile.name] + args + ['--'] + list(files),
                       onerr=Abort, errprefix=_("commit failed"))
            # refresh the index so that gitk doesn’t show empty staged diffs
            systemcall(['git', 'update-index', '--ignore-submodules', '-q', '--ignore-missing', '--unmerged', '--refresh'])

        finally:
            msgfile.unlink(missing_ok=True)


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
    parser.add_argument('-c', '--reedit-message', metavar='COMMIT', default=None, help='reuse and edit message from specified commit')
    parser.add_argument('-C', '--reuse-message', metavar='COMMIT', default=None, help='reuse message from specified commit')
    parser.add_argument('--fixup', metavar='COMMIT', default=None, help='create autosquash commit message to fixup specified commit')
    parser.add_argument('--reset-author', action='store_true', default=False, help='the commit is authored by me now (used with -C/-c/--amend)')
    parser.add_argument('-s', '--signoff', action='store_true', default=False, help='add Signed-off-by:')
    parser.add_argument('--amend', action='store_true', default=False, help='amend previous commit')
    parser.add_argument('-S', '--gpg-sign', metavar='KEY-ID', nargs='?', const=True, default=None, help='GPG sign commit')
    parser.add_argument('--no-gpg-sign', action='store_true', default=False, help=argparse.SUPPRESS)
    parser.add_argument('-v', '--verbose', default=0, action='count', help='be more verbose')
    parser.add_argument('--debug', action='store_const', const=2, dest='verbose', help='be debuggingly verbose')
    parser.add_argument('--cleanup', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--quiet', default=False, action='store_true', help='pass --quiet to git commit')
    parser.add_argument('--confirm', default=False, action='store_true', help='show confirmation prompt after selecting changes')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--cached', '--staged', action='store_true', default=False, help=argparse.SUPPRESS)
    group.add_argument('--index', action='store_true', default=False, help=argparse.SUPPRESS)
    args = parser.parse_args()

    opts = vars(args)
    opts['operation'] = subcommand

    if opts['fixup'] and opts['fixup'].startswith('reword:'):
        parser.error(_("Creating reword-only commits is not supported."))

    if subcommand == 'cstage':
        opts['index'] = True

    if subcommand == 'cunstage':
        opts['cached'] = True

    repo = GitRepo(".")
    ui = Ui(repo)
    ui.setdebuglevel(opts['verbose'])

    os.chdir(repo.path)

    try:
        crecord_core.dorecord(ui, repo, **opts)
    except Abort as inst:
        sinst = str(inst)
        if opts['quiet'] and 'commit failed' in sinst:
            sys.exit(5)
        else:
            sys.stderr.write(_("abort: %s\n") % sinst)
            sys.exit(1)
