#!/usr/bin/env python3

import os
import fnmatch
from distutils import log
from setuptools import setup, find_packages

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

def glob(fname):
    return fnmatch.filter(os.listdir(os.path.abspath(os.path.dirname(__file__))), fname)

def generate_manpage(src, dst):
    import docutils.core
    log.info("generating a manpage from %s to %s", src, dst)
    docutils.core.publish_file(source_path=src, destination_path=dst, writer_name='manpage')

def man_name(fname):
    import re
    matches = re.compile(r'^:Manual section: *([0-9]*)', re.MULTILINE).search(read(fname))
    if matches:
        section = matches.groups()[0]
    else:
        section = '7'
    base = os.path.splitext(fname)[0]
    manfname = base + '.' + section
    return manfname

def man_path(fname):
    category = fname.rsplit('.', 1)[1]
    return os.path.join('share', 'man', 'man' + category), [fname]

def man_files(pattern):
    return list(map(man_path, map(man_name, glob(pattern))))

# monkey patch setuptools to use distutils owner/group functionality
from setuptools.command import sdist
sdist_org = sdist.sdist
class sdist_new(sdist_org):
    def initialize_options(self):
        sdist_org.initialize_options(self)
        self.owner = self.group = 'root'
sdist.sdist = sdist_new

__manpages__ = 'git-*.rst'

from setuptools.command import build_py
build_py_org = build_py.build_py
class build_py_new(build_py_org):
    def run(self):
        build_py_org.run(self)
        if not self.dry_run:
            for page in glob(__manpages__):
                generate_manpage(page, man_name(page))
build_py.build_py = build_py_new

__name__ = "git-crecord"

setup(
    data_files = [
        (os.path.join('share', 'doc', __name__), glob('*.rst')),
        (os.path.join('share', 'doc', __name__), glob('*.png')),
        (os.path.join('share', 'doc', __name__), ['CONTRIBUTORS', 'COPYING'])
    ] + man_files(__manpages__)
)
