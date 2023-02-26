===========
git-crecord
===========

-----------------------------------------------
interactively select changes to commit or stage
-----------------------------------------------

:Author: Andrej Shadura <andrew@shadura.me>
:Date:   2022-03-20
:Version: 20230226.0
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

``--author=``\ *AUTHOR*

    Override the commit author. Specify an explicit author using the standard ``A U Thor <author@example.com>`` format. 
    Otherwise `AUTHOR` is assumed to be a pattern and is used to search for an existing commit by that author
    (i.e. ``rev-list --all -i --author=AUTHOR``); the commit author is then copied from the first such commit found.

``--date=``\ *DATE*

    Override the author date used in the commit.

``-m`` *MESSAGE*, ``--message=``\ *MESSAGE*

    Use the given `MESSAGE` as the commit message. If multiple ``-m`` options are given, their values are concatenated as separate paragraphs.

``-C`` *COMMIT*, ``--reuse-message=``\ *COMMIT*

    Reuse the commit message and the authorship information (including the timestamp) of the given commit.

``-c`` *COMMIT*, ``--reedit-message=``\ *COMMIT*

    Like ``-C``, but invoke an editor to allow the user to edit the commit message.

``--fixup=``\ *COMMIT*

    Automatically create the commit message by prepending "fixup!" to the commit message of the given commit.

``--reset-author``

    When used with ``-C``/``-c``/``--amend`` options, or when committing after a conflicting cherry-pick, declare that the
    authorship of the resulting commit now belongs to the committer. This also renews the author timestamp.

``-s``, ``--signoff``

    Add ``Signed-off-by`` line by the committer at the end of the commit log message.

``--amend``

    Amend previous commit. Replace the tip of the current branch by creating a new commit. The message from the original commit is used as
    the starting point, instead of an empty message, when no other message is specified from the command line via ``-m`` option. The new
    commit has the same parents and author as the current one.

``-S`` *KEY-ID*, ``--gpg-sign`` *KEY-ID*

    GPG-sign commits. The `KEY-ID` argument is optional and defaults to the committer identity.

``--no-gpg-sign``

    Don’t sign this commit even if `commit.gpgSign` is set.

``-v``, ``--verbose``

    Be more verbose.

``--debug``

    Show all sorts of debugging information. Implies ``--verbose``.

``-h``

    Show this help message and exit.

SEE ALSO
========

**git-commit**\(1)
