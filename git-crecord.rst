===========
git-crecord
===========

-----------------------------------------------
interactively select changes to commit or stage
-----------------------------------------------

:Author: Andrew Shadura <andrew@shadura.me>
:Date:   2016-12-25
:Version: 0.1
:Manual section: 1
:Manual group: Git

SYNOPSIS
========

**git crecord** [-h]

**git crecord** [-v] [--author=\ `AUTHOR`] [--date=\ `DATE`] [-m `MESSAGE`] [--amend] [-s]

DESCRIPTION
===========

**git-crecord** is a Git subcommand which allows users to interactively
select changes to commit or stage using a ncurses-based text user interface.
It is a port of the Mercurial crecord extension originally written by
Mark Edgington.

git-crecord allows you to interactively choose among the changes you have made
(with line-level granularity), and commit, stage or unstage only those changes
you select.
After committing or staging the selected changes, the unselected changes are
still present in your working copy, so you can use crecord multiple times to
split large changes into several smaller changesets.

OPTIONS
=======

--author=AUTHOR          Override the commit author. Specify an explicit author using the standard ``A U Thor <author@example.com>`` format.  Otherwise `AUTHOR` is assumed to be a pattern and is used to search for an existing commit by that author (i.e. ``rev-list --all -i --author=AUTHOR``); the commit author is then copied from the first such commit found.
--date=DATE              Override the author date used in the commit.
-m MESSAGE, --message=MESSAGE  Use the given `MESSAGE` as the commit message. If multiple ``-m`` options are given, their values are concatenated as separate paragraphs.
-s, --signoff            Add ``Signed-off-by`` line by the committer at the end of the commit log message.
--amend                  Amend previous commit. Replace the tip of the current branch by creating a new commit. The message from the original commit is used as the starting point, instead of an empty message, when no other message is specified from the command line via ``-m`` option. The new commit has the same parents and author as the current one.
-v, --verbose            Be more verbose.
--debug                  Show all sorts of debugging information. Implies ``--verbose``.
-h                       Show this help message and exit.

SEE ALSO
========

**git-commit**\(1)
