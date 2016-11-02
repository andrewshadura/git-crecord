import os
import sys
import util

INDEX_FILENAME = "index"

class GitTree(object):
    def __init__(self, tree):
        self._tree = tree

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self._tree)

    def read(self):
        util.system(['git', 'read-tree', '--reset',
                     self._tree], onerr=RuntimeError)

class GitIndex(object):
    def __init__(self, filename):
        self._filename = filename
        try:
            self._indextree = self.commit()

        except RuntimeError as inst:
            raise util.Abort('failed to read the index: %s' % inst)

    def __repr__(self):
        return "%s(%r, %r)" % (self.__class__.__name__, self._filename, self._indextree)

    def commit(self):
        return util.systemcall(['git', 'write-tree'], onerr=RuntimeError).rstrip('\n')

    def write(self):
        GitTree(self._indextree).read()

    def backup_tree(self):
        return self._indextree

class GitRepo(object):
    def __init__(self, path):
        try:
            self.path = util.systemcall(['git', 'rev-parse', '--show-toplevel'],
                                        onerr=util.Abort).rstrip('\n')
            self._controldir = util.systemcall(['git', 'rev-parse', '--git-dir']).rstrip('\n')
            if not os.path.isdir(self._controldir):
                raise util.Abort
        except util.Abort:
            sys.exit(1)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self.path)

    def controldir(self):
        return os.path.abspath(self._controldir)

    def index_path(self):
        return os.path.join(self.controldir(), INDEX_FILENAME)

    def open_index(self):
        return GitIndex(self.index_path())
