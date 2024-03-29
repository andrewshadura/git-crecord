#!/usr/bin/env python3

import fnmatch
import os

from setuptools import setup, distutils
from setuptools.command import build_py as build_py_orig

__manpages__ = 'git-*.rst'


def read(fname):
    with open(os.path.join(os.path.dirname(__file__), fname)) as f:
        return f.read()


def glob(fname):
    return fnmatch.filter(os.listdir(os.path.abspath(os.path.dirname(__file__))), fname)


def generate_manpage(src, dst):
    import docutils.core
    distutils.log.info("generating a manpage from %s as %s", src, dst)
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
    return [man_path(man_name(f)) for f in glob(pattern)]


# monkey patch setuptools to use distutils owner/group functionality
# and build the manpage on build

class build_py(build_py_orig.build_py):
    def run(self):
        super().run()
        if not self.dry_run:
            for page in glob(__manpages__):
                generate_manpage(page, man_name(page))


build_py_orig.build_py = build_py  # type: ignore

__name__ = "git-crecord"

setup(
    data_files=[
        (os.path.join('share', 'doc', __name__), glob('*.rst')),
        (os.path.join('share', 'doc', __name__), glob('*.png')),
        (os.path.join('share', 'doc', __name__), ['CONTRIBUTORS', 'COPYING']),
    ] + man_files(__manpages__),
)
