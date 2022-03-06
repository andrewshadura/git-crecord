# Git diff parser and related structures
#
# Copyright 2008—2011, 2014 Mark Edgington <edgimar@gmail.com>
# Copyright 2016, 2018—2022 Andrej Shadura <andrew@shadura.me>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# This code is based on the patch parser of Mercurial.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# stuff related specifically to patch manipulation / parsing
from gettext import gettext as _

import io
import re
from codecs import register_error

from typing import IO, Iterator, Optional, Sequence, Union

from .util import unwrap_filename

lines_re = re.compile(b'@@ -(\\d+)(?:,(\\d+))? \\+(\\d+)(?:,(\\d+))? @@\\s*(.*)')


class PatchError(Exception):
    pass


def hexreplace(err: UnicodeError) -> tuple[str, int]:
    if not isinstance(err, UnicodeDecodeError):
        raise NotImplementedError("only decoding is supported")
    return "".join(
        "<%X>" % x for x in err.object[err.start:err.end]
    ), err.end


register_error("hexreplace", hexreplace)


class LineReader:
    # simple class to allow pushing lines back into the input stream
    def __init__(self, fp: IO[bytes]):
        self.fp = fp
        self.buf: list[bytes] = []

    def push(self, line: bytes) -> None:
        if line is not None:
            self.buf.append(line)

    def readline(self) -> bytes:
        if self.buf:
            line = self.buf[0]
            del self.buf[0]
            return line
        return self.fp.readline()

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.readline, b'')


def scanpatch(fp: IO[bytes]):
    r"""Read a patch and yield the following events:

    - ('file',    [header_lines + fromfile + tofile])
    - ('context', [context_lines])
    - ('hunk',    [hunk_lines])
    - ('range',   (-start,len, +start,len, diffp))

    >>> rawpatch = b'''diff --git a/folder1/g b/folder1/g
    ... --- a/folder1/g
    ... +++ b/folder1/g
    ... @@ -1,8 +1,10 @@ some context
    ...  1
    ...  2
    ... -3
    ...  4
    ...  5
    ...  6
    ... +6.1
    ... +6.2
    ...  7
    ...  8
    ... +9'''
    >>> fp = io.BytesIO(rawpatch)
    >>> list(scanpatch(fp))
    [('file',
        [b'diff --git a/folder1/g b/folder1/g\n',
         b'--- a/folder1/g\n',
         b'+++ b/folder1/g\n']),
     ('range',
        (b'1', b'8', b'1', b'10', b'some context')),
     ('context',
        [b' 1\n', b' 2\n']),
     ('hunk',
        [b'-3\n']),
     ('context',
        [b' 4\n', b' 5\n', b' 6\n']),
     ('hunk',
        [b'+6.1\n', b'+6.2\n']),
     ('context',
        [b' 7\n', b' 8\n']),
     ('hunk',
        [b'+9'])]
    """
    lr = LineReader(fp)

    def scanwhile(first: bytes, p) -> list[bytes]:
        """scan lr while predicate holds"""
        lines = [first]
        for line in iter(lr.readline, b''):
            if p(line):
                lines.append(line)
            else:
                lr.push(line)
                break
        return lines

    for line in iter(lr.readline, b''):
        if line.startswith(b'diff --git a/') or line.startswith(b'diff --git "a/'):
            def notheader(line: bytes) -> bool:
                s = line.split(None, 1)
                return not s or s[0] not in (b'---', b'diff')

            header = scanwhile(line, notheader)
            fromfile = lr.readline()
            if fromfile.startswith(b'---'):
                tofile = lr.readline()
                header += [fromfile, tofile]
            else:
                lr.push(fromfile)
            yield 'file', header
        elif line.startswith(b' '):
            yield 'context', scanwhile(line, lambda l: l[0] in b' \\')
        elif line[0] in b'-+':
            yield 'hunk', scanwhile(line, lambda l: l[0] in b'-+\\')
        else:
            m = lines_re.match(line)
            if m:
                yield 'range', m.groups()
            else:
                raise PatchError('unknown patch content: %r' % line)


class PatchNode:
    """Abstract Class for Patch Graph Nodes
    (i.e. PatchRoot, header, hunk, HunkLine)
    """

    folded: bool
    # a patch this node belongs to
    patch: 'PatchRoot'

    def firstchild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def lastchild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def allchildren(self) -> Sequence['PatchNode']:
        """Return a list of all direct children of this node"""
        raise NotImplementedError("method must be implemented by subclass")

    def nextsibling(self) -> Optional['PatchNode']:
        """
        Return the closest next item of the same type where there are no items
        of different types between the current item and this closest item.
        If no such item exists, return None.
        """
        raise NotImplementedError("method must be implemented by subclass")

    def prevsibling(self) -> Optional['PatchNode']:
        """
        Return the closest previous item of the same type where there are no
        items of different types between the current item and this closest item.
        If no such item exists, return None.
        """
        raise NotImplementedError("method must be implemented by subclass")

    def parentitem(self) -> Optional['PatchNode']:
        """Return the parent to the current item"""
        raise NotImplementedError("method must be implemented by subclass")

    def nextitem(self, skipfolded=True) -> Optional['PatchNode']:
        """
        Try to return the next item closest to this item, regardless of item's
        type (header, hunk, or hunkline).

        If skipfolded == True, and the current item is folded, then the child
        items that are hidden due to folding will be skipped when determining
        the next item.

        If it is not possible to get the next item, return None.
        """
        try:
            itemfolded = self.folded
        except AttributeError:
            itemfolded = False
        if skipfolded and itemfolded:
            nextitem = self.nextsibling()
            if not nextitem:
                parent = self.parentitem()
                if parent:
                    nextitem = parent.nextsibling()
                else:
                    nextitem = None
            return nextitem
        else:
            # try child
            item = self.firstchild()
            if item:
                return item

            # else try next sibling
            item = self.nextsibling()
            if item:
                return item

            parent = self.parentitem()
            if parent:
                # else try parent's next sibling
                item = parent.nextsibling()
                if item:
                    return item

                # else return grandparent's next sibling (or None)
                grandparent = parent.parentitem()
                if grandparent:
                    return grandparent.nextsibling()
                else:
                    return None

            else:  # parent and/or grandparent was None
                return None

    def previtem(self) -> Optional['PatchNode']:
        """
        Try to return the previous item closest to this item, regardless of
        item's type (header, hunk, or hunkline).

        If it is not possible to get the previous item, return None.
        """
        # try previous sibling's last child's last child,
        # else try previous sibling's last child, else try previous sibling
        prevsibling = self.prevsibling()
        if prevsibling is not None:
            prevsiblinglastchild = prevsibling.lastchild()
            if ((prevsiblinglastchild is not None) and
                    not prevsibling.folded):
                prevsiblinglclc = prevsiblinglastchild.lastchild()
                if ((prevsiblinglclc is not None) and
                        not prevsiblinglastchild.folded):
                    return prevsiblinglclc
                else:
                    return prevsiblinglastchild
            else:
                return prevsibling

        # try parent (or None)
        return self.parentitem()

    def write(self, fp: IO[bytes]) -> None:
        """Write the unified diff-formatter representation of the
        patch node into the binary stream"""
        raise NotImplementedError("method must be implemented by subclass")

    def __bytes__(self) -> bytes:
        """Return the unified diff-formatter representation of the
        patch node as bytes"""
        with io.BytesIO() as b:
            self.write(b)
            return b.getvalue()


class Header(PatchNode):
    """Patch header"""
    diff_re = re.compile(b'diff --git (?P<fromfile>(?P<aq>")?a/.*(?(aq)"|)) (?P<tofile>(?P<bq>")?b/.*(?(bq)"|))$')
    allhunks_re = re.compile(b'(?:GIT binary patch|new file|deleted file) ')
    pretty_re = re.compile(b'(?:new file|deleted file) ')
    special_re = re.compile(b'(?:GIT binary patch|new|deleted|copy|rename) ')

    def __init__(self, header):
        self.header = header
        self.hunks = []
        # flag to indicate whether to apply this chunk
        self.applied = True
        # flag which only affects the status display indicating if a node's
        # children are partially applied (i.e. some applied, some not).
        self.partial = False

        # flag to indicate whether to display as folded/unfolded to user
        self.folded = True

        # flag is False if this header was ever unfolded from initial state
        self.neverunfolded = True

        # one-letter file status
        self._changetype = None

    def binary(self):
        """
        Return True if the file represented by the header is a binary file.
        Otherwise return False.

        """
        return any(h.startswith(b'GIT binary patch') for h in self.header)

    def pretty(self, fp: IO[str]):
        for h in self.header:
            if h.startswith(b'GIT binary patch'):
                fp.write(_('this modifies a binary file (all or nothing)\n'))
                break
            if self.pretty_re.match(h):
                fp.write(h.decode("UTF-8", errors="hexreplace"))
                if self.binary():
                    fp.write(_('this is a binary file\n'))
                break
            if h.startswith(b'---'):
                fp.write(_('%d hunks, %d lines changed\n') %
                         (len(self.hunks),
                          sum([max(h.added, h.removed) for h in self.hunks])))
                break
            fp.write(h.decode("UTF-8", errors="hexreplace"))

    def prettystr(self) -> str:
        return str(self)

    def __str__(self) -> str:
        with io.StringIO() as s:
            self.pretty(s)
            return s.getvalue()

    def write(self, fp: IO[bytes]) -> None:
        fp.write(b''.join(self.header))

    def allhunks(self) -> bool:
        """
        Return True if the file which the header represents was changed
        completely (i.e.  there is no possibility of applying a hunk of changes
        smaller than the size of the entire file.)  Otherwise, return False
        """
        return any(self.allhunks_re.match(h) for h in self.header)

    def files(self):
        fromfile, tofile = self.diff_re.match(self.header[0]).group('fromfile', 'tofile')
        fromfile = unwrap_filename(fromfile).removeprefix(b'a/')
        tofile = unwrap_filename(tofile).removeprefix(b'b/')
        if self.changetype == 'D':
            tofile = None
        elif self.changetype == 'A':
            fromfile = None
        return [fromfile, tofile]

    def filename(self) -> str:
        files = self.files()
        return (files[1] or files[0]).decode("UTF-8", errors="hexreplace")

    def __repr__(self) -> str:
        return '<header %s>' % (' '.join(
            repr(x) for x in self.files()
        ))

    def special(self) -> bool:
        return any(self.special_re.match(h) for h in self.header)

    @property
    def changetype(self) -> str:
        if self._changetype is None:
            self._changetype = "M"
            for h in self.header:
                if h.startswith(b'new file'):
                    self._changetype = "A"
                elif h.startswith(b'deleted file'):
                    self._changetype = "D"
                elif h.startswith(b'copy from'):
                    self._changetype = "C"
                elif h.startswith(b'rename from'):
                    self._changetype = "R"

        return self._changetype

    def nextsibling(self) -> Optional['Header']:
        numheadersinpatch = len(self.patch)
        indexofthisheader = self.patch.index(self)

        if indexofthisheader < numheadersinpatch - 1:
            nextheader = self.patch[indexofthisheader + 1]
            return nextheader
        else:
            return None

    def prevsibling(self) -> Optional['Header']:
        indexofthisheader = self.patch.index(self)
        if indexofthisheader > 0:
            previousheader = self.patch[indexofthisheader - 1]
            return previousheader
        else:
            return None

    def parentitem(self) -> None:
        """
        There is no 'real' parent item of a header that can be selected,
        so return None.
        """
        return None

    def firstchild(self):
        """Return the first child of this item, if one exists.  Otherwise, None."""
        if len(self.hunks) > 0:
            return self.hunks[0]
        else:
            return None

    def lastchild(self):
        """Return the last child of this item, if one exists.  Otherwise, None."""
        if len(self.hunks) > 0:
            return self.hunks[-1]
        else:
            return None

    def allchildren(self) -> Sequence['Hunk']:
        """Return a list of all direct children of this node"""
        return self.hunks


class HunkLine(PatchNode):
    """Represents a changed line in a hunk"""

    def __init__(self, linetext: bytes, hunk):
        self.linetext = linetext
        self.applied = True
        # the parent hunk to which this line belongs
        self.hunk = hunk
        # folding lines currently is not used/needed, but this flag is needed
        # in the prevItem method.
        self.folded = False

    def __bytes__(self):
        if self.applied:
            return self.linetext
        else:
            return b' ' + self.linetext[1:]

    @property
    def diffop(self):
        return self.linetext[0:1]

    def __str__(self) -> str:
        return self.prettystr()

    def prettystr(self) -> str:
        return self.linetext.decode("UTF-8", errors="hexreplace")

    def nextsibling(self):
        numlinesinhunk = len(self.hunk.changedlines)
        indexofthisline = self.hunk.changedlines.index(self)

        if indexofthisline < numlinesinhunk - 1:
            nextline = self.hunk.changedlines[indexofthisline + 1]
            return nextline
        else:
            return None

    def prevsibling(self):
        """Return the previous line in the hunk"""
        indexofthisline = self.hunk.changedlines.index(self)
        if indexofthisline > 0:
            previousline = self.hunk.changedlines[indexofthisline - 1]
            return previousline
        else:
            return None

    def parentitem(self):
        """Return the parent to the current item"""
        return self.hunk

    def firstchild(self):
        """Return the first child of this item, if one exists.  Otherwise, None."""
        # hunk-lines don't have children
        return None

    def lastchild(self):
        """Return the last child of this item, if one exists.  Otherwise, None."""
        # hunk-lines don't have children
        return None


class Hunk(PatchNode):
    """ui patch hunk, wraps a hunk and keeps track of ui behavior """
    maxcontext = 3
    header: Header
    fromline: int
    toline: int
    proc: bytes
    after: Sequence[bytes]
    before: Sequence[bytes]
    changedlines: Sequence[HunkLine]

    def __init__(
            self,
            header: Header,
            fromline: int,
            toline: int,
            proc: bytes,
            before: Sequence[bytes],
            hunklines: Sequence[bytes],
            after: Sequence[bytes]
    ):
        def trimcontext(number, lines):
            delta = len(lines) - self.maxcontext
            if False and delta > 0:
                return number + delta, lines[:self.maxcontext]
            return number, lines

        self.header = header
        self.fromline, self.before = trimcontext(fromline, before)
        self.toline, self.after = trimcontext(toline, after)
        self.proc = proc
        self.changedlines = [HunkLine(line, self) for line in hunklines]
        self.added, self.removed = self.countchanges()
        # used at end for detecting how many removed lines were un-applied
        self.originalremoved = self.removed

        # flag to indicate whether to display as folded/unfolded to user
        self.folded = True
        # flag to indicate whether to apply this chunk
        self.applied = True
        # flag which only affects the status display indicating if a node's
        # children are partially applied (i.e. some applied, some not).
        self.partial = False

    def nextsibling(self) -> Optional['Hunk']:
        """Return the next hunk in the group."""
        numhunksinheader = len(self.header.hunks)
        indexofthishunk = self.header.hunks.index(self)

        if indexofthishunk < numhunksinheader - 1:
            nexthunk = self.header.hunks[indexofthishunk + 1]
            return nexthunk
        else:
            return None

    def prevsibling(self) -> Optional['Hunk']:
        """Return the previous hunk in the group."""
        indexofthishunk = self.header.hunks.index(self)
        if indexofthishunk > 0:
            previoushunk = self.header.hunks[indexofthishunk - 1]
            return previoushunk
        else:
            return None

    def parentitem(self) -> Header:
        """Return the header for this hunk"""
        return self.header

    def firstchild(self) -> Optional[HunkLine]:
        """Return the first hunk line of this hunk, if one exists.  Otherwise, None."""
        if len(self.changedlines) > 0:
            return self.changedlines[0]
        else:
            return None

    def lastchild(self) -> Optional[HunkLine]:
        """Return the last child of this item, if one exists.  Otherwise, None."""
        if len(self.changedlines) > 0:
            return self.changedlines[-1]
        else:
            return None

    def allchildren(self) -> Sequence[PatchNode]:
        """Return a list of all direct children of this node"""
        return self.changedlines

    def countchanges(self) -> tuple[int, int]:
        """changedlines -> (n+,n-)"""
        add = len([line for line in self.changedlines if line.applied
                   and line.diffop == b'+'])
        rem = len([line for line in self.changedlines if line.applied
                   and line.diffop == b'-'])
        return add, rem

    def getfromtoline(self):
        """Calculate the number of removed lines converted to context lines"""
        removedconvertedtocontext = self.originalremoved - self.removed

        contextlen = (len(self.before) + len(self.after) +
                      removedconvertedtocontext)
        if self.after and self.after[-1] == b'\\ No newline at end of file\n':
            contextlen -= 1
        fromlen = contextlen + self.removed
        tolen = contextlen + self.added

        # Diffutils manual, section "2.2.2.2 Detailed Description of Unified
        # Format": "An empty hunk is considered to end at the line that
        # precedes the hunk."
        #
        # So, if either of hunks is empty, decrease its line start. --immerrr
        # But only do this if fromline > 0, to avoid having, e.g fromline=-1.
        fromline, toline = self.fromline, self.toline
        if fromlen == 0 and fromline > 0:
            fromline -= 1
        if tolen == 0 and toline > 0:
            toline -= 1

        fromtoline = b'@@ -%d,%d +%d,%d @@%b\n' % (
            fromline, fromlen, toline, tolen,
            self.proc and (b' ' + self.proc))

        return fromtoline

    def write(self, fp: IO[bytes]) -> None:
        # updated self.added/removed, which are used by getfromtoline()
        self.added, self.removed = self.countchanges()
        fp.write(self.getfromtoline())
        fp.write(b''.join(self.before))

        # add the following to the list: (1) all applied lines, and
        # (2) all unapplied removal lines (convert these to context lines)
        for changedline in self.changedlines:
            fp.write(bytes(changedline))

        fp.write(b''.join(self.after))

    def reversehunks(self) -> 'Hunk':
        r"""Make the hunk apply in the other direction.

        >>> header = Header([b'diff --git a/file b/file\n'])
        >>> print(Hunk(
        ...     header,
        ...     fromline=1,
        ...     toline=2,
        ...     proc=b'context',
        ...     before=[b' 1\n', b' 2\n'],
        ...     hunklines=[b'-3\n'],
        ...     after=[b' 4\n', b' 5\n'],
        ... ).reversehunks().prettystr())
        @@ -1,4 +2,5 @@ context
         1
         2
        +3
         4
         5
        """
        m = {b'+': b'-', b'-': b'+', b'\\': b'\\'}
        hunklines = [b'%s%s' % (m[line.linetext[0:1]], line.linetext[1:])
                     for line in self.changedlines if line.applied]
        return Hunk(self.header, self.fromline, self.toline, self.proc, self.before, hunklines, self.after)

    def files(self) -> list[Optional[bytes]]:
        return self.header.files()

    def filename(self) -> str:
        return self.header.filename()

    def __str__(self) -> str:
        return self.prettystr()

    def prettystr(self) -> str:
        x = io.BytesIO()
        self.write(x)
        return x.getvalue().decode("UTF-8", errors="hexreplace")

    def __repr__(self) -> str:
        return '<hunk %r@%d>' % (self.files()[1] or self.files()[0], self.fromline)


class PatchRoot(PatchNode, list):
    """List of header objects representing the patch."""

    def __init__(self, headerlist):
        super().__init__()
        self.extend(headerlist)
        # add parent patch object reference to each header
        for header in self:
            header.patch = self

    @property
    def headers(self) -> Sequence[Header]:
        return [c for c in self if isinstance(c, Header)]

    @property
    def hunks(self) -> Sequence[Hunk]:
        return [c for c in self if isinstance(c, Hunk)]


def parsepatch(fp: IO[bytes]) -> PatchRoot:
    r"""Parse a patch, returning a list of header and hunk objects.

    >>> rawpatch = b'''diff --git a/folder1/g b/folder1/g
    ... --- a/folder1/g
    ... +++ b/folder1/g
    ... @@ -1,8 +1,10 @@
    ...  1
    ...  2
    ... -3
    ...  4
    ...  5
    ...  6
    ... +6.1
    ... +6.2
    ...  7
    ...  8
    ... +9'''
    >>> fp = io.BytesIO(rawpatch)
    >>> headers = parsepatch(fp)

    Headers and hunks are interspersed in the list returned from
    the function:
    >>> headers
    [<header b'folder1/g' b'folder1/g'>,
     <hunk b'folder1/g'@1>,
     <hunk b'folder1/g'@7>,
     <hunk b'folder1/g'@9>]

    >>> headers[0].filename()
    'folder1/g'

    Each header also provides a list of hunks belonging to it:
    >>> headers[0].hunks
    [<hunk b'folder1/g'@1>,
     <hunk b'folder1/g'@7>,
     <hunk b'folder1/g'@9>]
    >>> out = io.BytesIO()
    >>> for header in headers:
    ...     header.write(out)
    >>> print(out.getvalue().decode("ascii"))
    diff --git a/folder1/g b/folder1/g
    --- a/folder1/g
    +++ b/folder1/g
    @@ -1,6 +1,5 @@
     1
     2
    -3
     4
     5
     6
    @@ -7,2 +6,4 @@
    +6.1
    +6.2
     7
     8
    @@ -8,0 +10,1 @@
    +9

    It is possible to handle non-UTF-8 patches:
    >>> rawpatch = b'''diff --git a/test b/test
    ... --- /dev/null
    ... +++ b/test
    ... @@ -0,0 +1,2 @@
    ... +\xCD\xCE\xCD-\xD3\xD2\xD4-8 \xF2\xE5\xF1\xF2
    ... +test'''
    >>> fp = io.BytesIO(rawpatch)
    >>> headers = parsepatch(fp)
    >>> out = io.BytesIO()
    >>> for header in headers:
    ...     header.write(out)

    Non-UTF-8 characters survive the roundtrip:
    >>> print(out.getvalue().decode("cp1251"))
    diff --git a/test b/test
    --- /dev/null
    +++ b/test
    @@ -0,0 +1,2 @@
    +НОН-УТФ-8 тест
    +test

    When pretty-printing the hunk, they get replaced with their
    hexadecimal codes:
    >>> print(headers[0].hunks[0])
    @@ -0,0 +1,2 @@
    +<CD><CE><CD>-<D3><D2><D4>-8 <F2><E5><F1><F2>
    +test

    Quoted filenames in the diff headers are supported too:
    >>> rawpatch = b'''diff --git "a/test- \\\\\\321\\217\\321\\217" "b/test- \\\\\\321\\217\\321\\217"
    ... new file mode 100644
    ... index 000000000000..7f53c853ca78
    ... --- /dev/null
    ... +++ "b/test- \\\\\\321\\217\\321\\217"
    ... @@ -0,0 +1,2 @@
    ... +\xCD\xCE\xCD-\xD3\xD2\xD4-8 \xF2\xE5\xF1\xF2
    ... +test'''
    >>> fp = io.BytesIO(rawpatch)
    >>> patch = parsepatch(fp)
    >>> files = patch.headers[0].files()
    >>> files[0]
    >>> files[1].decode('UTF-8')
    'test- \\яя'
    """

    class Parser:
        """patch parsing state machine"""

        header: Header
        headers: Sequence[Union[Header, Hunk]]

        def __init__(self):
            self.fromline = 0
            self.toline = 0
            self.proc = b''
            self.context = []
            self.before = []
            self.hunk = []
            self.headers = []

        def addrange(self, limits):
            """Store range line info to associated instance variables."""
            fromstart, fromend, tostart, toend, proc = limits
            self.fromline = int(fromstart)
            self.toline = int(tostart)
            self.proc = proc

        def add_new_hunk(self):
            """
            Create a new complete hunk object, adding it to the latest header
            and to self.headers.

            Add all of the previously collected information about
            the hunk to the new hunk object.  This information includes
            header, from/to-lines, function (self.proc), preceding context
            lines, changed lines, as well as the current context lines (which
            follow the changed lines).

            The size of the from/to lines are updated to be correct for the
            next hunk we parse.

            """
            h = Hunk(self.header, self.fromline, self.toline, self.proc,
                     self.before, self.hunk, self.context)
            self.header.hunks.append(h)
            self.headers.append(h)
            self.fromline += len(self.before) + h.removed + len(self.context)
            self.toline += len(self.before) + h.added + len(self.context)
            self.before = []
            self.hunk = []
            self.context = []
            self.proc = b''

        def addcontext(self, context):
            """
            Set the value of self.context.

            Also, if an unprocessed set of changelines was previously
            encountered, this is the condition for creating a complete
            hunk object.  In this case, we create and add a new hunk object to
            the most recent header object, and to self.strem. 

            """
            self.context = context
            # if there have been changed lines encountered that haven't yet
            # been add to a hunk.
            if self.hunk:
                self.add_new_hunk()

        def addhunk(self, hunk):
            """
            Store the changed lines in self.changedlines.

            Mark any context lines in the context-line buffer (self.context) as
            lines preceding the changed-lines (i.e. stored in self.before), and
            clear the context-line buffer.

            """
            self.hunk = hunk
            self.before = self.context
            self.context = []

        def newfile(self, header):
            """
            Create a header object containing the header lines, and the
            filename the header applies to.  Add the header to self.headers.

            """
            # if there are any lines in the unchanged-lines buffer, create a 
            # new hunk using them, and add it to the last header.
            if self.hunk:
                self.add_new_hunk()

            # create a new header and add it to self.header
            h = Header(header)
            self.headers.append(h)
            self.header = h

        def finished(self):
            # if there are any lines in the unchanged-lines buffer, create a 
            # new hunk using them, and add it to the last header.
            if self.hunk:
                self.add_new_hunk()

            return self.headers

        transitions = {
            'file': {'context': addcontext,
                     'file': newfile,
                     'hunk': addhunk,
                     'range': addrange},
            'context': {'file': newfile,
                        'hunk': addhunk,
                        'range': addrange},
            'hunk': {'context': addcontext,
                     'file': newfile,
                     'range': addrange},
            'range': {'context': addcontext,
                      'hunk': addhunk},
        }

    p = Parser()

    # run the state-machine
    state = 'context'
    for newstate, data in scanpatch(fp):
        try:
            p.transitions[state][newstate](p, data)
        except KeyError:
            raise PatchError('unhandled transition: %s -> %s' %
                             (state, newstate))
        state = newstate
    return PatchRoot(p.finished())


def filterpatch(opts, patch: PatchRoot, chunkselector, ui):
    r"""Interactively filter patch chunks into applied-only chunks

    >>> rawpatch = b'''diff --git a/dir/file.c b/dir/file.c
    ... index e548702cb275..28208f7ff2ac 100644
    ... --- a/dir/file.c
    ... +++ b/dir/file.c
    ... @@ -1684,11 +1684,13 @@ int function()
    ...  1
    ...  2
    ...  3
    ... -4
    ... -5
    ... -6
    ... -7
    ...  8
    ... +9
    ... +10
    ... +11
    ... +12
    ... +13
    ... +14
    ...  15
    ...  16
    ...  17
    ... '''
    >>> patch = parsepatch(io.BytesIO(rawpatch))
    >>> patch
    [<header b'dir/file.c' b'dir/file.c'>,
     <hunk b'dir/file.c'@1684>,
     <hunk b'dir/file.c'@1692>]
    >>> def selector(opts, headers, ui):
    ...     headers[0].hunks[0].applied = False
    ... 
    >>> applied = filterpatch(None, patch, selector, None)
    >>> applied
    [<header b'dir/file.c' b'dir/file.c'>,
     <hunk b'dir/file.c'@1692>]
    >>> print(applied.hunks[0])
    @@ -1692,3 +1692,9 @@
    +9
    +10
    +11
    +12
    +13
    +14
     15
     16
     17
    """
    # if there are no changed files
    if len(patch) == 0:
        return []

    # let user choose headers/hunks/lines, and mark their applied flags accordingly
    chunkselector(opts, patch.headers, ui)

    applied_hunks = PatchRoot([])
    for header in patch.headers:
        if (header.applied and
                (header.special() or header.binary() or len([
                    h for h in header.hunks if h.applied
                ]) > 0)):
            applied_hunks.append(header)
            fixoffset = 0
            for hunk in header.hunks:
                if hunk.applied:
                    applied_hunks.append(hunk)
                    # adjust the 'to'-line offset of the hunk to be correct
                    # after de-activating some other hunks for this file
                    if fixoffset:
                        # hunk = copy.copy(hunk) # necessary??
                        hunk.toline += fixoffset
                else:
                    fixoffset += hunk.removed - hunk.added

    return applied_hunks
