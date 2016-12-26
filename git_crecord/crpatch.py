# stuff related specifically to patch manipulation / parsing
from gettext import gettext as _

import cStringIO
import re

class PatchError(Exception):
    pass

lines_re = re.compile(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@\s*(.*)')

class linereader(object):
    # simple class to allow pushing lines back into the input stream
    def __init__(self, fp):
        self.fp = fp
        self.buf = []

    def push(self, line):
        if line is not None:
            self.buf.append(line)

    def readline(self):
        if self.buf:
            l = self.buf[0]
            del self.buf[0]
            return l
        return self.fp.readline()

    def __iter__(self):
        while True:
            l = self.readline()
            if not l:
                break
            yield l

def scanpatch(fp):
    """like patch.iterhunks, but yield different events

    - ('file',    [header_lines + fromfile + tofile])
    - ('context', [context_lines])
    - ('hunk',    [hunk_lines])
    - ('range',   (-start,len, +start,len, diffp))
    """
    lr = linereader(fp)

    def scanwhile(first, p):
        """scan lr while predicate holds"""
        lines = [first]
        while True:
            line = lr.readline()
            if not line:
                break
            if p(line):
                lines.append(line)
            else:
                lr.push(line)
                break
        return lines

    while True:
        line = lr.readline()
        if not line:
            break
        if line.startswith('diff --git a/'):
            def notheader(line):
                s = line.split(None, 1)
                return not s or s[0] not in ('---', 'diff')
            header = scanwhile(line, notheader)
            fromfile = lr.readline()
            if fromfile.startswith('---'):
                tofile = lr.readline()
                header += [fromfile, tofile]
            else:
                lr.push(fromfile)
            yield 'file', header
        elif line[0] == ' ':
            yield 'context', scanwhile(line, lambda l: l[0] in ' \\')
        elif line[0] in '-+':
            yield 'hunk', scanwhile(line, lambda l: l[0] in '-+\\')
        else:
            m = lines_re.match(line)
            if m:
                yield 'range', m.groups()
            else:
                raise PatchError('unknown patch content: %r' % line)

class patchnode(object):
    """Abstract Class for Patch Graph Nodes
    (i.e. PatchRoot, header, hunk, HunkLine)
    """

    def firstchild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def lastchild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def allchildren(self):
        "Return a list of all of the direct children of this node"
        raise NotImplementedError("method must be implemented by subclass")

    def nextsibling(self):
        """
        Return the closest next item of the same type where there are no items
        of different types between the current item and this closest item.
        If no such item exists, return None.
        """
        raise NotImplementedError("method must be implemented by subclass")

    def prevsibling(self):
        """
        Return the closest previous item of the same type where there are no
        items of different types between the current item and this closest item.
        If no such item exists, return None.
        """
        raise NotImplementedError("method must be implemented by subclass")

    def parentitem(self):
        raise NotImplementedError("method must be implemented by subclass")

    def nextitem(self, skipfolded=True):
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
            if nextitem is None:
                try:
                    nextitem = self.parentitem().nextsibling()
                except AttributeError:
                    nextitem = None
            return nextitem
        else:
            # try child
            item = self.firstchild()
            if item is not None:
                return item

            # else try next sibling
            item = self.nextsibling()
            if item is not None:
                return item

            try:
                # else try parent's next sibling
                item = self.parentitem().nextsibling()
                if item is not None:
                    return item

                # else return grandparent's next sibling (or None)
                return self.parentitem().parentitem().nextsibling()

            except AttributeError: # parent and/or grandparent was None
                return None

    def previtem(self):
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

class patch(patchnode, list): # TODO: rename PatchRoot
    """
    List of header objects representing the patch.

    """
    def __init__(self, headerlist):
        self.extend(headerlist)
        # add parent patch object reference to each header
        for header in self:
            header.patch = self

class uiheader(patchnode):
    """patch header

    XXX shoudn't we move this to mercurial/patch.py ?
    """
    diff_re = re.compile('diff --git a/(.*) b/(.*)$')
    allhunks_re = re.compile('(?:GIT binary patch|new file|deleted file) ')
    pretty_re = re.compile('(?:new file|deleted file) ')
    special_re = re.compile('(?:GIT binary patch|new|deleted|copy|rename) ')

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

        # list of all headers in patch
        self.patch = None

        # flag is False if this header was ever unfolded from initial state
        self.neverunfolded = True

        # one-letter file status
        self._changetype = None

    def binary(self):
        """
        Return True if the file represented by the header is a binary file.
        Otherwise return False.

        """
        for h in self.header:
            if h.startswith('GIT binary patch'):
                return True
        return False

    def pretty(self, fp):
        for h in self.header:
            if h.startswith('GIT binary patch'):
                fp.write(_('this modifies a binary file (all or nothing)\n'))
                break
            if self.pretty_re.match(h):
                fp.write(h)
                if self.binary():
                    fp.write(_('this is a binary file\n'))
                break
            if h.startswith('---'):
                fp.write(_('%d hunks, %d lines changed\n') %
                         (len(self.hunks),
                          sum([h.added + h.removed for h in self.hunks])))
                break
            fp.write(h)

    def prettystr(self):
        x = cStringIO.StringIO()
        self.pretty(x)
        return x.getvalue()

    def write(self, fp):
        fp.write(''.join(self.header))

    def allhunks(self):
        """
        Return True if the file which the header represents was changed
        completely (i.e.  there is no possibility of applying a hunk of changes
        smaller than the size of the entire file.)  Otherwise return False

        """
        for h in self.header:
            if self.allhunks_re.match(h):
                return True
        return False

    def files(self):
        fromfile, tofile = self.diff_re.match(self.header[0]).groups()
        if self.changetype == 'D':
            tofile = None
        elif self.changetype == 'A':
            fromfile = None
        return [fromfile, tofile]

    def filename(self):
        files = self.files()
        return files[1] or files[0]

    def __repr__(self):
        return '<header %s>' % (' '.join(map(repr, self.files())))

    def special(self):
        for h in self.header:
            if self.special_re.match(h):
                return True

    @property
    def changetype(self):
        if self._changetype is None:
            self._changetype = "M"
            for h in self.header:
                if h.startswith('new file'):
                    self._changetype = "A"
                elif h.startswith('deleted file'):
                    self._changetype = "D"
                elif h.startswith('copy from'):
                    self._changetype = "C"
                elif h.startswith('rename from'):
                    self._changetype = "R"

        return self._changetype

    def nextsibling(self):
        numheadersinpatch = len(self.patch)
        indexofthisheader = self.patch.index(self)

        if indexofthisheader < numheadersinpatch - 1:
            nextheader = self.patch[indexofthisheader + 1]
            return nextheader
        else:
            return None

    def prevsibling(self):
        indexofthisheader = self.patch.index(self)
        if indexofthisheader > 0:
            previousheader = self.patch[indexofthisheader - 1]
            return previousheader
        else:
            return None

    def parentitem(self):
        """
        There is no 'real' parent item of a header that can be selected,
        so return None.
        """
        return None

    def firstchild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        if len(self.hunks) > 0:
            return self.hunks[0]
        else:
            return None

    def lastchild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        if len(self.hunks) > 0:
            return self.hunks[-1]
        else:
            return None

    def allchildren(self):
        "Return a list of all of the direct children of this node"
        return self.hunks

class uihunkline(patchnode):
    "Represents a changed line in a hunk"
    def __init__(self, linetext, hunk):
        self.linetext = linetext
        self.applied = True
        # the parent hunk to which this line belongs
        self.hunk = hunk
        # folding lines currently is not used/needed, but this flag is needed
        # in the prevItem method.
        self.folded = False

    def prettystr(self):
        return self.linetext

    def nextsibling(self):
        numlinesinhunk = len(self.hunk.changedlines)
        indexofthisline = self.hunk.changedlines.index(self)

        if (indexofthisline < numlinesinhunk - 1):
            nextline = self.hunk.changedlines[indexofthisline + 1]
            return nextline
        else:
            return None

    def prevsibling(self):
        indexofthisline = self.hunk.changedlines.index(self)
        if indexofthisline > 0:
            previousline = self.hunk.changedlines[indexofthisline - 1]
            return previousline
        else:
            return None

    def parentitem(self):
        "Return the parent to the current item"
        return self.hunk

    def firstchild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        # hunk-lines don't have children
        return None

    def lastchild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        # hunk-lines don't have children
        return None

class uihunk(patchnode):
    """ui patch hunk, wraps a hunk and keep track of ui behavior """
    maxcontext = 3

    def __init__(self, header, fromline, toline, proc, before, hunk, after):
        def trimcontext(number, lines):
            delta = len(lines) - self.maxcontext
            if False and delta > 0:
                return number + delta, lines[:self.maxcontext]
            return number, lines

        self.header = header
        self.fromline, self.before = trimcontext(fromline, before)
        self.toline, self.after = trimcontext(toline, after)
        self.proc = proc
        self.changedlines = [uihunkline(line, self) for line in hunk]
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

    def nextsibling(self):
        numhunksinheader = len(self.header.hunks)
        indexofthishunk = self.header.hunks.index(self)

        if (indexofthishunk < numhunksinheader - 1):
            nexthunk = self.header.hunks[indexofthishunk + 1]
            return nexthunk
        else:
            return None

    def prevsibling(self):
        indexofthishunk = self.header.hunks.index(self)
        if indexofthishunk > 0:
            previoushunk = self.header.hunks[indexofthishunk - 1]
            return previoushunk
        else:
            return None

    def parentitem(self):
        "Return the parent to the current item"
        return self.header

    def firstchild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        if len(self.changedlines) > 0:
            return self.changedlines[0]
        else:
            return None

    def lastchild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        if len(self.changedlines) > 0:
            return self.changedlines[-1]
        else:
            return None

    def allchildren(self):
        "Return a list of all of the direct children of this node"
        return self.changedlines

    def countchanges(self):
        """changedlines -> (n+,n-)"""
        add = len([l for l in self.changedlines if l.applied
                   and l.prettystr()[0] == '+'])
        rem = len([l for l in self.changedlines if l.applied
                   and l.prettystr()[0] == '-'])
        return add, rem

    def getfromtoline(self):
        # calculate the number of removed lines converted to context lines
        removedconvertedtocontext = self.originalremoved - self.removed

        contextlen = (len(self.before) + len(self.after) +
                      removedconvertedtocontext)
        if self.after and self.after[-1] == '\\ No newline at end of file\n':
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
        if fromlen == 0:
            if fromline != 0:
                fromline -= 1
        if tolen == 0:
            if toline != 0:
                toline -= 1

        fromtoline = '@@ -%d,%d +%d,%d @@%s\n' % (
            fromline, fromlen, toline, tolen,
            self.proc and (' ' + self.proc))
        return fromtoline

    def write(self, fp):
        # updated self.added/removed, which are used by getfromtoline()
        self.added, self.removed = self.countchanges()
        fp.write(self.getfromtoline())

        hunklinelist = []
        # add the following to the list: (1) all applied lines, and
        # (2) all unapplied removal lines (convert these to context lines)
        for changedline in self.changedlines:
            changedlinestr = changedline.prettystr()
            if changedline.applied:
                hunklinelist.append(changedlinestr)
            elif changedlinestr[0] == "-":
                hunklinelist.append(" " + changedlinestr[1:])

        fp.write(''.join(self.before + hunklinelist + self.after))

    pretty = write

    def filename(self):
        return self.header.filename()

    def prettystr(self):
        x = cStringIO.StringIO()
        self.pretty(x)
        return x.getvalue()

    def __repr__(self):
        return '<hunk %r@%d>' % (self.filename(), self.fromline)



def parsepatch(fp):
    "Parse a patch, returning a list of header and hunk objects."
    class parser(object):
        """patch parsing state machine"""
        def __init__(self):
            self.fromline = 0
            self.toline = 0
            self.proc = ''
            self.header = None
            self.context = []
            self.before = []
            self.changedlines = []
            self.stream = []

        def _range(self, (fromstart, fromend, tostart, toend, proc)):
            "Store range line info to associated instance variables."
            self.fromline = int(fromstart)
            self.toline = int(tostart)
            self.proc = proc

        def add_new_hunk(self):
            """
            Create a new complete hunk object, adding it to the latest header
            and to self.stream.

            Add all of the previously collected information about
            the hunk to the new hunk object.  This information includes
            header, from/to-lines, function (self.proc), preceding context
            lines, changed lines, as well as the current context lines (which
            follow the changed lines).

            The size of the from/to lines are updated to be correct for the
            next hunk we parse.

            """
            h = uihunk(self.header, self.fromline, self.toline, self.proc,
                       self.before, self.changedlines, self.context)
            self.header.hunks.append(h)
            self.stream.append(h)
            self.fromline += len(self.before) + h.removed + len(self.context)
            self.toline += len(self.before) + h.added + len(self.context)
            self.before = []
            self.changedlines = []
            self.context = []
            self.proc = ''

        def _context(self, context):
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
            if self.changedlines:
                self.add_new_hunk()

        def _changedlines(self, changedlines):
            """
            Store the changed lines in self.changedlines.

            Mark any context lines in the context-line buffer (self.context) as
            lines preceding the changed-lines (i.e. stored in self.before), and
            clear the context-line buffer.

            """
            self.changedlines = changedlines
            self.before = self.context
            self.context = []

        def add_new_header(self, hdr):
            """
            Create a header object containing the header lines, and the
            filename the header applies to.  Add the header to self.stream.

            """
            # if there are any lines in the unchanged-lines buffer, create a 
            # new hunk using them, and add it to the last header.
            if self.changedlines:
                self.add_new_hunk()

            # create a new header and add it to self.stream
            self.header = uiheader(hdr)
            fileName = self.header.filename()

            self.stream.append(self.header)

        def finished(self):
            # if there are any lines in the unchanged-lines buffer, create a 
            # new hunk using them, and add it to the last header.
            if self.changedlines:
                self.add_new_hunk()

            return self.stream

        transitions = {
            'file': {'context': _context,
                     'file': add_new_header,
                     'hunk': _changedlines,
                     'range': _range},
            'context': {'file': add_new_header,
                        'hunk': _changedlines,
                        'range': _range},
            'hunk': {'context': _context,
                     'file': add_new_header,
                     'range': _range},
            'range': {'context': _context,
                      'hunk': _changedlines},
            }

    p = parser()

    # run the state-machine
    state = 'context'
    for newstate, data in scanpatch(fp):
        try:
            p.transitions[state][newstate](p, data)
        except KeyError:
            raise PatchError('unhandled transition: %s -> %s' %
                                   (state, newstate))
        state = newstate
    return p.finished()

def filterpatch(opts, chunks, chunkselector, ui):
    """Interactively filter patch chunks into applied-only chunks"""
    chunks = list(chunks)
    # convert chunks list into structure suitable for displaying/modifying
    # with curses.  Create a list of headers only.
    headers = [c for c in chunks if isinstance(c, uiheader)]

    # if there are no changed files
    if len(headers) == 0:
        return []

    # let user choose headers/hunks/lines, and mark their applied flags accordingly
    chunkselector(opts, headers, ui)

    appliedHunkList = []
    for hdr in headers:
        if (hdr.applied and
            (hdr.special() or len([h for h in hdr.hunks if h.applied]) > 0)):
            appliedHunkList.append(hdr)
            fixoffset = 0
            for hnk in hdr.hunks:
                if hnk.applied:
                    appliedHunkList.append(hnk)
                    # adjust the 'to'-line offset of the hunk to be correct
                    # after de-activating some of the other hunks for this file
                    if fixoffset:
                        #hnk = copy.copy(hnk) # necessary??
                        hnk.toline += fixoffset
                else:
                    fixoffset += hnk.removed - hnk.added

    return appliedHunkList
