# Git wrapper for repo/tree/index manipulation
#
# Copyright 2016, 2018—2022 Andrej Shadura <andrew@shadura.me>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import util

INDEX_FILENAME = "index"

ObjectHash = str


class GitTree:
    def __init__(self, tree):
        self._tree = tree

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self._tree)

    def read(self):
        util.system(['git', 'read-tree', '--reset',
                     self._tree], onerr=RuntimeError)


class GitIndex:
    def __init__(self, filename):
        self._filename = filename
        self.indextree = None

    def __repr__(self):
        return "%s(%r, %r)" % (self.__class__.__name__, self._filename, self.indextree)

    def commit(self) -> ObjectHash:
        return util.systemcall(
            ['git', 'write-tree'],
            onerr=RuntimeError,
            encoding="ascii",
        ).rstrip('\n')

    def write(self):
        GitTree(self.indextree).read()

    def backup_tree(self) -> ObjectHash:
        try:
            self.indextree = self.commit()
        except RuntimeError as inst:
            raise util.Abort('failed to read the index: %s' % inst)
        return self.indextree


class GitRepo:
    def __init__(self, path: os.PathLike | str | None):
        try:
            self.path = Path(util.systemcall(
                ['git', 'rev-parse', '--show-toplevel'],
                dir=path,
                encoding="fs",
                onerr=util.Abort
            ).rstrip('\n'))
            self._controldir = Path(util.systemcall(
                ['git', 'rev-parse', '--git-dir'],
                dir=path,
                encoding="fs",
            ).rstrip('\n'))
            if not self._controldir.is_dir():
                raise util.Abort
        except util.Abort:
            sys.exit(1)

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.path)

    @property
    def controldir(self) -> Path:
        return self._controldir.resolve()

    @property
    def index_path(self) -> Path:
        return self.controldir / INDEX_FILENAME

    def open_index(self) -> GitIndex:
        return GitIndex(self.index_path)

    def head(self) -> ObjectHash:
        return util.systemcall(
            ['git', 'rev-parse', '--verify', '-q', 'HEAD'],
            encoding="ascii",
        ).rstrip('\n')
