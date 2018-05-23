#!/usr/bin/python2

import os
import fnmatch
from setuptools import setup, find_packages

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

def glob(fname):
    return fnmatch.filter(os.listdir(os.path.abspath(os.path.dirname(__file__))), fname)

def generate_manpage(src, dst):
    import docutils.core
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
    return map(man_path, map(man_name, glob(pattern)))

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
            map(lambda x: generate_manpage(*x), map(lambda x: (x, man_name(x)), glob(__manpages__)))
build_py.build_py = build_py_new

__name__ = "git-crecord"

setup(
    name = __name__,
    version = "20161226.0",
    author = 'Andrew Shadura',
    author_email = 'andrew@shadura.me',
    url = 'https://github.com/andrewshadura/git-crecord',
    description = 'interactively select chunks to commit with Git',
    long_description = read('README.rst'),
    license = 'GPL-2+',
    packages = find_packages(),
    setup_requires = ['docutils>=0.12'],
    classifiers = [
        'Development Status :: 4 - Beta',
        'Environment :: Console :: Curses',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Version Control',
    ],
    include_package_data = True,
    data_files = [
        (os.path.join('share', 'doc', __name__), glob('*.rst')),
        (os.path.join('share', 'doc', __name__), glob('*.png')),
        (os.path.join('share', 'doc', __name__), ['CONTRIBUTORS', 'COPYING'])
    ] + man_files(__manpages__),
    entry_points = {
        'console_scripts': [
            'git-crecord = git_crecord.main:main'
        ]
    }
)
