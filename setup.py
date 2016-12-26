#!/usr/bin/python2

import os
import subprocess
import docutils.core
from glob import glob as abs_glob
from setuptools import setup, find_packages

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

def glob(fname):
    return abs_glob(os.path.join(os.path.dirname(__file__), fname))

def generate_manpage(fname):
    import re
    matches = re.compile(r'^:Manual section: *([0-9]*)', re.MULTILINE).search(read(fname))
    if matches:
        section = matches.groups()[0]
    else:
        section = '7'
    base = os.path.splitext(fname)[0]
    manfname = base + '.' + section
    docutils.core.publish_file(source_path=fname, destination_path=manfname, writer_name='manpage')
    return manfname

def man_path(fname):
    category = fname.rsplit('.', 1)[1]
    return os.path.join('share', 'man', 'man' + category), [fname]

def man_files(pattern):
    return map(man_path, map(generate_manpage, glob(pattern)))

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
    install_requires = ['docutils>=0.12'],
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
    ] + man_files('git-*.rst'),
    entry_points = {
        'console_scripts': [
            'git-crecord = git_crecord.main:main'
        ]
    }
)
