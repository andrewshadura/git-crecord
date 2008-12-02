# crecord.py
#
# Copyright 2008 Mark Edgington <edgimar@gmail.com>
#
# This software may be used and distributed according to the terms of
# the GNU General Public License, incorporated herein by reference.
#
# Much of this extension is based on Bryan O'Sullivan's record extension.

'''text-gui based change selection during commit or qrefresh'''
from mercurial.i18n import gettext, _
from mercurial import cmdutil, commands, extensions, hg, mdiff, patch
from mercurial import util
import copy, cStringIO, errno, operator, os, re, tempfile
import curses
import signal

lines_re = re.compile(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\s*(.*)')

def scanpatch(fp):
    """like patch.iterhunks, but yield different events

    - ('file',    [header_lines + fromfile + tofile])
    - ('context', [context_lines])
    - ('hunk',    [hunk_lines])
    - ('range',   (-start,len, +start,len, diffp))
    """
    lr = patch.linereader(fp)

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
                raise patch.PatchError('unknown patch content: %r' % line)

class PatchNode(object):
    "Abstract Class for Patch Graph Nodes (i.e. PatchRoot, header, hunk, HunkLine)"
    
    def firstChild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def lastChild(self):
        raise NotImplementedError("method must be implemented by subclass")

    def nextSibling(self):
        """
        Return the closest next item of the same type where there are no items
        of different types between the current item and this closest item.
        If no such item exists, return None.
        
        """
        raise NotImplementedError("method must be implemented by subclass")

    def prevSibling(self):
        """
        Return the closest previous item of the same type where there are no
        items of different types between the current item and this closest item.
        If no such item exists, return None.
        
        """
        raise NotImplementedError("method must be implemented by subclass")

    def parentItem(self):
        raise NotImplementedError("method must be implemented by subclass")
    
    
    def nextItem(self, constrainLevel=True, skipFolded=True):
        """
        If constrainLevel == True, return the closest next item
        of the same type where there are no items of different types between
        the current item and this closest item.
        
        If constrainLevel == False, then try to return the next item
        closest to this item, regardless of item's type (header, hunk, or
        HunkLine).
        
        If skipFolded == True, and the current item is folded, then the child
        items that are hidden due to folding will be skipped when determining
        the next item.
        
        If it is not possible to get the next item, return None.
        
        """
        try:
            itemFolded = self.folded
        except AttributeError:
            itemFolded = False
        if constrainLevel:
            return self.nextSibling()
        elif skipFolded and itemFolded:
            nextItem = self.nextSibling()
            if nextItem is None:
                try:
                    nextItem = self.parentItem().nextSibling()
                except AttributeError:
                    nextItem = None
            return nextItem
        else:
            # try child
            item = self.firstChild()
            if item is not None:
                return item
            
            # else try next sibling
            item = self.nextSibling()
            if item is not None:
                return item
            
            try:
                # else try parent's next sibling
                item = self.parentItem().nextSibling()
                if item is not None:
                    return item
                
                # else return grandparent's next sibling (or None)
                return self.parentItem().parentItem().nextSibling()

            except AttributeError: # parent and/or grandparent was None
                return None

    def prevItem(self, constrainLevel=True, skipFolded=True):
        """
        If constrainLevel == True, return the closest previous item
        of the same type where there are no items of different types between
        the current item and this closest item.
        
        If constrainLevel == False, then try to return the previous item
        closest to this item, regardless of item's type (header, hunk, or
        HunkLine).

        If skipFolded == True, and the current item is folded, then the items
        that are hidden due to folding will be skipped when determining the
        next item.

        If it is not possible to get the previous item, return None.
        
        """
        if constrainLevel:
            return self.prevSibling()
        else:
            # try previous sibling's last child's last child,
            # else try previous sibling's last child, else try previous sibling
            prevSibling = self.prevSibling()
            if prevSibling is not None:
                prevSiblingLastChild = prevSibling.lastChild()
                if (prevSiblingLastChild is not None) and not prevSibling.folded:
                    prevSiblingLCLC = prevSiblingLastChild.lastChild()
                    if (prevSiblingLCLC is not None) and not prevSiblingLastChild.folded:
                        return prevSiblingLCLC
                    else:
                        return prevSiblingLastChild
                else:
                    return prevSibling
            
            # try parent (or None)
            return self.parentItem()

class Patch(PatchNode, list): # TODO: rename PatchRoot
    """
    List of header objects representing the patch.
    
    """
    def __init__(self, headerList):
        self.extend(headerList)
        # add parent patch object reference to each header
        for header in self:
            header.patch = self

class header(PatchNode):
    """patch header

    XXX shoudn't we move this to mercurial/patch.py ?
    """
    diff_re = re.compile('diff --git a/(.*) b/(.*)$')
    allhunks_re = re.compile('(?:index|new file|deleted file) ')
    pretty_re = re.compile('(?:new file|deleted file) ')
    special_re = re.compile('(?:index|new|deleted|copy|rename) ')

    def __init__(self, header):
        self.header = header
        self.hunks = []
        # flag to indicate whether to apply this chunk
        self.applied = True
        # flag to indicate whether to display as folded/unfolded to user
        self.folded = False
        
        # list of all headers in patch
        self.patch = None

    def binary(self):
        """
        Return True if the file represented by the header is a binary file.
        Otherwise return False.
        
        """
        for h in self.header:
            if h.startswith('index '):
                return True
        return False

    def pretty(self, fp):
        for h in self.header:
            if h.startswith('index '):
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

    def prettyStr(self):
        x = cStringIO.StringIO()
        self.pretty(x)
        return x.getvalue()
        
    def write(self, fp):
        fp.write(''.join(self.header))

    def allhunks(self):
        """
        Return True if the file which the header represents was changed completely (i.e.
        there is no possibility of applying a hunk of changes smaller than the size of the
        entire file.)  Otherwise return False
        
        """
        for h in self.header:
            if self.allhunks_re.match(h):
                return True
        return False

    def files(self):
        fromfile, tofile = self.diff_re.match(self.header[0]).groups()
        if fromfile == tofile:
            return [fromfile]
        return [fromfile, tofile]

    def filename(self):
        return self.files()[-1]

    def __repr__(self):
        return '<header %s>' % (' '.join(map(repr, self.files())))

    def special(self):
        for h in self.header:
            if self.special_re.match(h):
                return True

    def nextSibling(self):
        numHeadersInPatch = len(self.patch)
        indexOfThisHeader = self.patch.index(self)
        
        if indexOfThisHeader < numHeadersInPatch - 1:
            nextHeader = self.patch[indexOfThisHeader + 1]
            return nextHeader
        else:
            return None

    def prevSibling(self):
        indexOfThisHeader = self.patch.index(self)
        if indexOfThisHeader > 0:
            previousHeader = self.patch[indexOfThisHeader - 1]
            return previousHeader
        else:
            return None

    def parentItem(self):
        """
        There is no 'real' parent item of a header that can be selected,
        so return None.
        """
        return None
    
    def firstChild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        if len(self.hunks) > 0:
            return self.hunks[0]
        else:
            return None

    def lastChild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        if len(self.hunks) > 0:
            return self.hunks[-1]
        else:
            return None

class HunkLine(PatchNode):
    "Represents a changed line in a hunk"
    def __init__(self, lineText, hunk):
        self.lineText = lineText
        self.applied = True
        # the parent hunk to which this line belongs
        self.hunk = hunk
        # folding lines currently is not used/needed, but this flag is needed
        # in the prevItem method.
        self.folded = False
    
    def prettyStr(self):
        return self.lineText

    def nextSibling(self):
        numLinesInHunk = len(self.hunk.changedLines)
        indexOfThisLine = self.hunk.changedLines.index(self)
        
        if (indexOfThisLine < numLinesInHunk - 1):
            nextLine = self.hunk.changedLines[indexOfThisLine + 1]
            return nextLine
        else:
            return None
    
    def prevSibling(self):
        indexOfThisLine = self.hunk.changedLines.index(self)
        if indexOfThisLine > 0:
            previousLine = self.hunk.changedLines[indexOfThisLine - 1]
            return previousLine
        else:
            return None
    
    def parentItem(self):
        "Return the parent to the current item"
        return self.hunk

    def firstChild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        # hunk-lines don't have children
        return None

    def lastChild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        # hunk-lines don't have children
        return None

class hunk(PatchNode):
    """patch hunk

    XXX shouldn't we merge this with patch.hunk ?
    """
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
        self.hunk = hunk
        self.changedLines = [HunkLine(line, self) for line in hunk]
        self.added, self.removed = self.countchanges(self.hunk)

        # flag to indicate whether to display as folded/unfolded to user
        self.folded = False
        # flag to indicate whether to apply this chunk
        self.applied = True

    def nextSibling(self):
        numHunksInHeader = len(self.header.hunks)
        indexOfThisHunk = self.header.hunks.index(self)

        if (indexOfThisHunk < numHunksInHeader - 1):
            nextHunk = self.header.hunks[indexOfThisHunk + 1]
            return nextHunk
        else:
            return None

    def prevSibling(self):
        indexOfThisHunk = self.header.hunks.index(self)
        if indexOfThisHunk > 0:
            previousHunk = self.header.hunks[indexOfThisHunk - 1]
            return previousHunk
        else:
            return None

    def parentItem(self):
        "Return the parent to the current item"
        return self.header

    def firstChild(self):
        "Return the first child of this item, if one exists.  Otherwise None."
        if len(self.changedLines) > 0:
            return self.changedLines[0]
        else:
            return None

    def lastChild(self):
        "Return the last child of this item, if one exists.  Otherwise None."
        if len(self.changedLines) > 0:
            return self.changedLines[-1]
        else:
            return None
    
    @staticmethod
    def countchanges(hunk):
        """hunk -> (n+,n-)"""
        add = len([h for h in hunk if h[0] == '+'])
        rem = len([h for h in hunk if h[0] == '-'])
        return add, rem

    def getFromToLine(self):
        delta = len(self.before) + len(self.after)
        if self.after and self.after[-1] == '\\ No newline at end of file\n':
            delta -= 1
        fromlen = delta + self.removed
        tolen = delta + self.added
        fromToLine = '@@ -%d,%d +%d,%d @@%s\n' % \
                 (self.fromline, fromlen, self.toline, tolen,
                  self.proc and (' ' + self.proc))
        return fromToLine

    def write(self, fp):
        fp.write(self.getFromToLine())
        fp.write(''.join(self.before + self.hunk + self.after))

    pretty = write

    def filename(self):
        return self.header.filename()
    
    def prettyStr(self):
        x = cStringIO.StringIO()
        self.pretty(x)
        return x.getvalue()

    def __repr__(self):
        return '<hunk %r@%d>' % (self.filename(), self.fromline)

def parsepatch(fp):
    """patch -> [] of hunks """
    class parser(object):
        """patch parsing state machine"""
        def __init__(self):
            self.fromline = 0
            self.toline = 0
            self.proc = ''
            self.header = None
            self.context = []
            self.before = []
            self.hunk = []
            self.stream = []

        def addrange(self, (fromstart, fromend, tostart, toend, proc)):
            self.fromline = int(fromstart)
            self.toline = int(tostart)
            self.proc = proc

        def addcontext(self, context):
            if self.hunk:
                h = hunk(self.header, self.fromline, self.toline, self.proc,
                         self.before, self.hunk, context)
                self.header.hunks.append(h)
                self.stream.append(h)
                self.fromline += len(self.before) + h.removed
                self.toline += len(self.before) + h.added
                self.before = []
                self.hunk = []
                self.proc = ''
            self.context = context

        def addhunk(self, hunk):
            if self.context:
                self.before = self.context
                self.context = []
            self.hunk = hunk

        def newfile(self, hdr):
            self.addcontext([])
            h = header(hdr)
            self.stream.append(h)
            self.header = h

        def finished(self):
            self.addcontext([])
            return self.stream

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

    p = parser()

    state = 'context'
    for newstate, data in scanpatch(fp):
        try:
            p.transitions[state][newstate](p, data)
        except KeyError:
            raise patch.PatchError('unhandled transition: %s -> %s' %
                                   (state, newstate))
        state = newstate
    return p.finished()

def filterpatch(ui, chunks):
    """Interactively filter patch chunks into applied-only chunks"""
    chunks = list(chunks)
    # convert chunks list into structure suitable for displaying/modifying
    # with curses.  Create a list of headers only.
    headers = [c for c in chunks if isinstance(c, header)]
    
    # let user choose headers/hunks/lines, and mark their applied flags accordingly
    selectChunks(headers)
    
    appliedHunkList = []
    for hdr in headers:
        if hdr.applied and (hdr.special() or len([h for h in hdr.hunks if h.applied]) > 0):
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

def selectChunks(headerList):
    """
    Curses interface to get selection of chunks, and mark the applied flags
    of the chosen chunks.
    
    """
    stdscr = curses.initscr()
    curses.start_color()
    
    chunkSelector = CursesChunkSelector(headerList)
    curses.wrapper(chunkSelector.main)

class CursesChunkSelector(object):
    def __init__(self, headerList):
        # put the headers into a patch object
        self.headerList = Patch(headerList)
        
        # list of all chunks
        self.chunkList = []
        for h in headerList:
            self.chunkList.append(h)
            self.chunkList.extend(h.hunks)
        
        # dictionary mapping (fgColor,bgColor) pairs to the corresponding curses
        # color-pair value.
        self.colorPairs = {}
        # maps custom nicknames of color-pairs to curses color-pair values
        self.colorPairNames = {}
        
        # the currently selected header, hunk, or hunk-line
        self.currentSelectedItem = self.headerList[0]
        
        # updated when printing out patch-display -- the 'lines' here are the
        # line positions *in the pad*, not on the screen.
        self.selectedItemStartLine = 0
        self.selectedItemEndLine = None
        
        # define indentation levels
        self.headerIndentNumChars = 0
        self.hunkIndentNumChars = 3
        self.hunkLineIndentNumChars = 6
        
        # the first line of the pad to print to the screen
        self.firstLineOfPadToPrint = 0
        
        # keeps track of the number of lines in the pad
        self.numPadLines = None
        
        self.numStatusLines = 2
                
        # keep a running count of the number of lines printed to the pad
        # (used for determining when the selected item begins/ends)
        self.linesPrintedToPadSoFar = 0
        
        # the first line of the pad which is visible on the screen
        self.firstLineOfPadToPrint = 0
    
    def upArrowEvent(self):
        """
        Try to select the previous item to the current item that has the
        most-indented level.  For example, if a hunk is selected, try to select
        the last HunkLine of the hunk prior to the selected hunk.  Or, if
        the first HunkLine of a hunk is currently selected, then select the
        hunk itself.
        
        If the currently selected item is already at the top of the screen, 
        scroll the screen down to show the new-selected item.
        
        """
        currentItem = self.currentSelectedItem
        
        nextItem = currentItem.prevItem(constrainLevel=False)
        
        if nextItem is None:
            # if no parent item (i.e. currentItem is the first header), then
            # no change...
            nextItem = currentItem
        
        self.currentSelectedItem = nextItem
    
    def upArrowShiftEvent(self):
        """
        Select (if possible) the previous item on the same level as the currently
        selected item.  Otherwise, select (if possible) the parent-item of the
        currently selected item.
        
        If the currently selected item is already at the top of the screen, 
        scroll the screen down to show the new-selected item.
        
        """
        currentItem = self.currentSelectedItem
        nextItem = currentItem.prevItem()
        # if there's no previous item on this level, try choosing the parent
        if nextItem is None:
            nextItem = currentItem.parentItem()
        if nextItem is None:
            # if no parent item (i.e. currentItem is the first header), then
            # no change...
            nextItem = currentItem
        
        self.currentSelectedItem = nextItem

    def downArrowEvent(self):
        """
        Try to select the next item to the current item that has the
        most-indented level.  For example, if a hunk is selected, select
        the first HunkLine of the selected hunk.  Or, if the last HunkLine of
        a hunk is currently selected, then select the next hunk, if one exists,
        or if not, the next header if one exists.
        
        If the currently selected item is already at the bottom of the screen, 
        scroll the screen up to show the new-selected item.
        
        """
        #self.startPrintLine += 1 #DEBUG
        currentItem = self.currentSelectedItem
        
        nextItem = currentItem.nextItem(constrainLevel=False)
        # if there's no next item, keep the selection as-is
        if nextItem is None:
            nextItem = currentItem
        
        self.currentSelectedItem = nextItem

    def downArrowShiftEvent(self):
        """
        If the cursor is already at the bottom chunk, scroll the screen up and move the cursor-position
        to the subsequent chunk.  Otherwise, only move the cursor position down one chunk.

        """
        # TODO: update docstring
        
        currentItem = self.currentSelectedItem
        nextItem = currentItem.nextItem()
        # if there's no previous item on this level, try choosing the parent's
        # nextItem.
        if nextItem is None:
            try:
                nextItem = currentItem.parentItem().nextItem()
            except AttributeError:
                # parentItem returned None, so nextItem() can't be called
                nextItem = None
        if nextItem is None:
            # if no next item on parent-level, then no change...
            nextItem = currentItem

        self.currentSelectedItem = nextItem

    def rightArrowEvent(self):
        """
        Select (if possible) the first of this item's child-items.
        
        """
        currentItem = self.currentSelectedItem
        nextItem = currentItem.firstChild()
        
        # turn off folding if we want to show a child-item
        if currentItem.folded:
            currentItem.folded = False
        
        if nextItem is None:
            # if no next item on parent-level, then no change...
            nextItem = currentItem

        self.currentSelectedItem = nextItem

    def leftArrowEvent(self):
        """
        Select (if possible) the parent of this item.
        
        """
        currentItem = self.currentSelectedItem
        nextItem = currentItem.parentItem()
        
        if nextItem is None:
            # if no next item on parent-level, then no change...
            nextItem = currentItem

        self.currentSelectedItem = nextItem

    def updateScroll(self):
        "Scroll the screen in such a way to fully show the currently-selected item."
        selStart = self.selectedItemStartLine
        selEnd = self.selectedItemEndLine
        padStart = self.firstLineOfPadToPrint
        padEnd = padStart + self.yScreenSize - self.numStatusLines - 1
        
        if selEnd > padEnd:
            self.scrollLines(selEnd - padEnd)
        elif selStart < padStart:
            # negative values scroll in pgup direction
            self.scrollLines(selStart - padStart)
        

    def scrollLines(self, numLines):
        "Scroll the screen up (down) by numLines when numLines >0 (<0)."
        self.firstLineOfPadToPrint += numLines
        if self.firstLineOfPadToPrint < 0:
            self.firstLineOfPadToPrint = 0
        if self.firstLineOfPadToPrint > self.numPadLines-1:
            self.firstLineOfPadToPrint = self.numPadLines-1

    def toggleApply(self, item=None):
        """
        Toggle the applied flag of the specified item.  If no item is specified,
        toggle the flag of the currently selected item.
        
        """
        if item is None:
            item = self.currentSelectedItem
        # for now, disable line-toggling until this is supported by the commit code
        if not isinstance(item, HunkLine):
            item.applied = not item.applied
        
        if isinstance(item, header):
            if item.applied and not item.special():
                # apply all its hunks
                for hnk in item.hunks:
                    hnk.applied = True
                    # apply all their HunkLines
                    for hunkLine in hnk.changedLines:
                        hunkLine.applied = True
            else:
                # un-apply all its hunks
                for hnk in item.hunks:
                    hnk.applied = False
                    # un-apply all their HunkLines
                    for hunkLine in hnk.changedLines:
                        hunkLine.applied = False
        elif isinstance(item, hunk):
            # apply all it's HunkLines
            for hunkLine in item.changedLines:
                hunkLine.applied = item.applied
            # if all 'sibling' hunks are not-applied
            if not (True in [hnk.applied for hnk in item.header.hunks]) and \
                                                    not item.header.special():
                item.header.applied = False
            # apply the header if it's not applied and we're applying a child hunk
            if item.applied and not item.header.applied:
                item.header.applied = True
        elif isinstance(item, HunkLine):
            # if all 'sibling' lines are not-applied
            if not (True in [hnkln.applied for hnkln in item.hunk.changedLines]):
                item.hunk.applied = False
            # apply the hunk if it's not applied and we're applying a child line
            if item.applied and not item.hunk.applied:
                item.hunk.applied = True
            # if all parent hunks are not applied, un-apply header
            if not (True in [hnk.applied for hnk in item.hunk.header.hunks]) and \
                                                    not item.hunk.header.special():
                item.hunk.header.applied = False
            # apply the header if it's not applied and we're applying a child hunk
            if item.hunk.applied and not item.hunk.header.applied:
                item.hunk.header.applied = True
    
    def toggleFolded(self, item=None, foldParent=False):
        "Toggle folded flag of specified item (defaults to currently selected)"
        if item is None:
            item = self.currentSelectedItem
        if not isinstance(item, header) and foldParent:
            item = item.parentItem()
            # we need to select the parent item in this case
            self.currentSelectedItem = item
        
        if isinstance(item, (header, hunk)):
            item.folded = not item.folded
        

    def alignString(self, inStr):
        """
        Add whitespace to the end of a string in order to make it fill
        the screen in the x direction.  The current cursor position is
        taken into account when making this calculation.  The string can span
        multiple lines.
        
        """
        y,xStart = self.chunkpad.getyx()
        width = self.xScreenSize
        strLen = len(inStr)
        numSpaces = (width - ((strLen + xStart) % width))
        return inStr + " " * numSpaces

    def printString(self, window, text, fgColor=None, bgColor=None, pair=None, pairName=None, attrList=None, toWin=True):
        """
        Print the string, text, with the specified colors and attributes, to
        the specified curses window object.
        
        The foreground and background colors are of the form
        curses.COLOR_XXXX, where XXXX is one of: [BLACK, BLUE, CYAN, GREEN,
        MAGENTA, RED, WHITE, YELLOW].  If pairName is provided, a color
        pair will be looked up in the self.colorPairNames dictionary.
        
        attrList is a list containing text attributes in the form of 
        curses.A_XXXX, where XXXX can be: [BOLD, DIM, NORMAL, STANDOUT,
        UNDERLINE].
        
        """
        if not toWin: # if we shouldn't print to the window
            return text
        if pair is not None:
            colorPair = pair
        elif pairName is not None:
            colorPair = self.colorPairNames[pairName]
        else:
            if fgColor is None:
                fgColor = curses.COLOR_WHITE
            if bgColor is None:
                bgColor = curses.COLOR_BLACK
            if self.colorPairs.has_key((fgColor,bgColor)):
                colorPair = self.colorPairs[(fgColor,bgColor)]
            else:
                colorPair = self.getColorPair(fgColor, bgColor)
        # add attributes if possible
        if attrList is None:
            attrList = []
        if colorPair < 256:
            # then it is safe to apply all attributes
            for textAttr in attrList:
                colorPair |= textAttr
        else:
            # just apply a select few (safe?) attributes
            for textAttr in (curses.A_UNDERLINE, curses.A_BOLD):
                if textAttr in attrList:
                    colorPair |= textAttr
                
        y,xStart = self.chunkpad.getyx()
        textlen=len(text) #debug
        width = self.xScreenSize #debug
        
        linesPrinted = (xStart + len(text)) / self.xScreenSize
        # is reset to 0 at the beginning of printItem()
        self.linesPrintedToPadSoFar += linesPrinted

        window.addstr(text, colorPair)
        return text


    def updateScreen(self):
        self.statuswin.erase()
        self.chunkpad.erase()

        width = self.xScreenSize
        alignString = self.alignString
        printString = self.printString

        # print out the status lines at the top
        try:
            printString(self.statuswin, alignString("SELECT CHUNKS: (j/k/up/down/pgup/pgdn) move cursor; (space) toggle applied"), pairName="legend")
            printString(self.statuswin, alignString(" (f)old/unfold; (c)ommit applied; (q)uit; (?) help | [X]=hunk applied **=folded"), pairName="legend")
        except curses.error:
            pass
        
        # print out the patch in the remaining part of the window
        try:
            self.printItem()
            self.updateScroll()
            self.chunkpad.refresh(self.firstLineOfPadToPrint,0,self.numStatusLines,0,self.yScreenSize+1-self.numStatusLines,self.xScreenSize)
        except curses.error:
            pass
        
        # refresh([pminrow, pmincol, sminrow, smincol, smaxrow, smaxcol])
        self.statuswin.refresh()

    def getStatusPrefixString(self, item):
        """
        Create a string to prefix a line with which indicates whether 'item'
        is applied and/or folded.
        
        """
        # create checkBox string
        if item.applied:
            checkBox = "[X]"
        else:
            checkBox = "[ ]"

        try:
            if item.folded:
                checkBox += "**"
            else:
                checkBox += "  "
        except AttributeError: # not foldable
            checkBox += "  "
        
        return checkBox

    def printHeader(self, header, selected=False, countLines=False, toWin=True):
        """
        Print the header to the pad.  If countLines is True, don't print
        anything, but just count the number of lines which would be printed.
        
        """
        outStr = ""
        text = header.prettyStr()
        chunkIndex = self.chunkList.index(header)
        
        if chunkIndex != 0:
            # add separating line before headers
            outStr += self.printString(self.chunkpad, '_'*self.xScreenSize, toWin=toWin)
        # select color-pair based on if the header is selected
        if selected:
            colorPair = self.getColorPair(name="selected", attrList=[curses.A_BOLD])
        else:
            colorPair = self.getColorPair(name="normal", attrList=[curses.A_BOLD])

        # print out each line of the chunk, expanding it to screen width
        
        # number of characters to indent lines on this level by
        indentNumChars = 0
        checkBox = self.getStatusPrefixString(header)
        textList = text.split("\n")
        lineStr = checkBox + textList[0]
        outStr += self.printString(self.chunkpad, self.alignString(lineStr), pair=colorPair, toWin=toWin)
        if len(textList) > 1:
            for line in textList[1:]:
                lineStr = " "*(indentNumChars + len(checkBox)) + line
                outStr += self.printString(self.chunkpad, self.alignString(lineStr), pair=colorPair, toWin=toWin)
        
        return outStr
    
    def printHunkLinesBefore(self, hunk, selected=False, toWin=True):
        "includes start/end line indicator"
        outStr = ""
        # where hunk is in list of siblings
        hunkIndex = hunk.header.hunks.index(hunk)
        
        if hunkIndex != 0:
            # add separating line before headers
            outStr += self.printString(self.chunkpad, ' '*self.xScreenSize, toWin=toWin)

        if selected:
            colorPair = self.getColorPair(name="selected", attrList=[curses.A_BOLD])
        else:
            colorPair = self.getColorPair(name="normal", attrList=[curses.A_BOLD])
            
        # print out from-to line with checkbox
        checkBox = self.getStatusPrefixString(hunk)

        linePrefix = " "*self.hunkIndentNumChars + checkBox
        frToLine = "   " + hunk.getFromToLine().strip("\n")
                
                
        outStr += self.printString(self.chunkpad, linePrefix, toWin=toWin) # add uncolored checkbox/indent
        outStr += self.printString(self.chunkpad, self.alignString(frToLine), pair=colorPair, toWin=toWin)

        if hunk.folded:
            # skip remainder of output
            return outStr
        
        # print out lines of the chunk preceeding changed-lines
        for line in hunk.before:
            lineStr = " "*(self.hunkLineIndentNumChars + len(checkBox)) + line.strip("\n")
            outStr += self.printString(self.chunkpad, self.alignString(lineStr), toWin=toWin)
        
        return outStr
        
    def printHunkLinesAfter(self, hunk, toWin=True):
        outStr = ""
        if hunk.folded:
            return outStr
        
        indentNumChars = self.hunkLineIndentNumChars
        # a bit superfluous, but to avoid hard-coding indent amount
        checkBox = self.getStatusPrefixString(hunk)
        for line in hunk.after:
            lineStr = " "*(indentNumChars + len(checkBox)) + line.strip("\n")
            outStr += self.printString(self.chunkpad, self.alignString(lineStr), toWin=toWin)
        
        return outStr
        
    def printHunkChangedLine(self, hunkLine, selected=False, toWin=True):
        outStr = ""
        indentNumChars = self.hunkLineIndentNumChars
        checkBox = self.getStatusPrefixString(hunkLine)
        
        lineStr = hunkLine.prettyStr().strip("\n")
        
        # select color-pair based on whether line is an addition/removal
        if selected:
            colorPair = self.getColorPair(name="selected")
        elif lineStr.startswith("+"):
            colorPair = self.getColorPair(name="addition")
        elif lineStr.startswith("-"):
            colorPair = self.getColorPair(name="deletion")
        
        linePrefix = " "*indentNumChars + checkBox
        outStr += self.printString(self.chunkpad, linePrefix, toWin=toWin) # add uncolored checkbox/indent
        outStr += self.printString(self.chunkpad, self.alignString(lineStr), pair=colorPair, toWin=toWin)
        return outStr

    def printItem(self, item=None, ignoreFolding=False, recurseChildren=True, toWin=True):
        """
        Use __printItem() to print the the specified item.applied.
        If item is not specified, then print the entire patch.
        (hiding folded elements, etc. -- see __printitem() docstring)
        """
        if item is None:
            item = self.headerList
        if recurseChildren:
            self.linesPrintedToPadSoFar = 0
            global outStr
        retStr = self.__printItem(item, ignoreFolding, recurseChildren, toWin=toWin)
        if recurseChildren:
            # remove the string when finished, so it doesn't accumulate
            del outStr
        
        return retStr
    
    def __printItem(self, item, ignoreFolding, recurseChildren, toWin=True):
        """
        Recursive method for printing out patch/header/hunk/hunk-line data to
        screen.  Also returns a string with all of the content of the displayed
        patch (not including coloring, etc.).
        
        If ignoreFolding is True, then folded items are printed out.
        
        If recurseChildren is False, then only print the item without its
        child items.
        
        """
        # keep outStr local, since we're not recursing
        if recurseChildren:
            global outStr
            try:
                outStr
            except:
                outStr = ""
        else:
            outStr = ""
        
        selected = (item is self.currentSelectedItem)
        if selected and recurseChildren:
            # assumes line numbering starting from line 0
            self.selectedItemStartLine = self.linesPrintedToPadSoFar
            selectedItemLines = self.getNumLinesDisplayed(item, recurseChildren=False)
            self.selectedItemEndLine = self.selectedItemStartLine + selectedItemLines - 1
        
        # Patch object is a list of headers
        if isinstance(item, Patch):
            if recurseChildren:
                for hdr in item:
                    self.__printItem(hdr, ignoreFolding, recurseChildren, toWin)
        if isinstance(item, header):
            outStr += self.printHeader(item, selected, toWin=toWin)
            if recurseChildren:
                for hnk in item.hunks:
                    self.__printItem(hnk, ignoreFolding, recurseChildren, toWin)
        elif isinstance(item, hunk) and \
        ((not item.header.folded) or ignoreFolding):
            # print the hunk data which comes before the changed-lines
            outStr += self.printHunkLinesBefore(item, selected, toWin=toWin)
            if recurseChildren:
                for line in item.changedLines:
                    self.__printItem(line, ignoreFolding, recurseChildren, toWin)
                outStr += self.printHunkLinesAfter(item, toWin=toWin)
        elif isinstance(item, HunkLine) and ((not item.hunk.folded) or ignoreFolding):
            outStr += self.printHunkChangedLine(item, selected, toWin=toWin)

        return outStr

    def getNumLinesDisplayed(self, item=None, ignoreFolding=True, recurseChildren=True):
        """
        Return the number of lines which would be displayed if the item were
        to be printed to the display.  The item will NOT be printed to the
        display (pad).
        If no item is given, assume the entire patch.
        If ignoreFolding is True, folded items will be unfolded when counting
        the number of lines.
        
        """
        # temporarily disable printing to windows by printString
        patchDisplayString = self.printItem(item, ignoreFolding, recurseChildren, toWin=False)
        numLines = len(patchDisplayString)/self.xScreenSize
        return numLines
    
    def sigwinchHandler(self, n, frame):
        "Handle window resizing"
        try:
            curses.endwin()
            self.stdscr = curses.initscr()
            self.yScreenSize, self.xScreenSize = self.stdscr.getmaxyx()

            self.statuswin = curses.newwin(self.numStatusLines,self.xScreenSize,0,0)
        except curses.error:
            pass
            # TODO: make resizing to a smaller width work (also for help screen)
            # re-calculate an upper-bound on the number of lines in the pad
            #self.numPadLines = self.getNumLinesDisplayed()
            #self.chunkpad = curses.newpad(self.numPadLines, self.xScreenSize)
            #self.updateScreen()
    
    def getColorPair(self, fgColor=None, bgColor=None, name=None, attrList=None):
        """
        Get a curses color pair, adding it to self.colorPairs if it is not already
        defined.  An optional string, name, can be passed as a shortcut for
        referring to the color-pair.  By default, if no arguments are specified,
        the white foreground / black background color-pair is returned.
        
        It is expected that this function will be used exclusively for initializing
        color pairs, and NOT curses.init_pair().
        
        attrList is used to 'flavor' the returned color-pair.  This information
        is not stored in self.colorPairs.  It contains attribute values like
        curses.A_BOLD.
        
        """
        if (name is not None) and self.colorPairNames.has_key(name):
            # then get the associated color pair and return it
            colorPair = self.colorPairNames[name]
        else:
            if fgColor is None:
                fgColor = curses.COLOR_WHITE
            if bgColor is None:
                bgColor = curses.COLOR_BLACK
            if self.colorPairs.has_key((fgColor,bgColor)):
                colorPair = self.colorPairs[(fgColor,bgColor)]
            else:
                pairIndex = len(self.colorPairs) + 1
                curses.init_pair(pairIndex, fgColor, bgColor)
                colorPair = self.colorPairs[(fgColor, bgColor)] = curses.color_pair(pairIndex)
                if name is not None:
                    self.colorPairNames[name] = curses.color_pair(pairIndex)
        
        # add attributes if possible
        if attrList is None:
            attrList = []
        if colorPair < 256:
            # then it is safe to apply all attributes
            for textAttr in attrList:
                colorPair |= textAttr
        else:
            # just apply a select few (safe?) attributes
            for textAttrib in (curses.A_UNDERLINE, curses.A_BOLD):
                if textAttrib in attrList:
                    colorPair |= textAttrib
        return colorPair
    
    def initColorPair(self, *args, **kwargs):
        "Same as getColorPair."
        self.getColorPair(*args, **kwargs)    

    def helpWindow(self):
        "Print a help window to the screen.  Exit after any keypress."
        helpText = """            [press any key to return to the patch-display]

crecord allows you to interactively choose among the changes you have made,
and commit only those changes you select.  After committing the selected
changes, the unselected changes are still present in your working copy, so you
can use crecord multiple times to split large changes into smaller changesets.
The following are valid keystrokes:

                [SPACE] : toggle selection of an item ([X] means selected)
    Up/Down-arrow [k/j] : go to previous/next unfolded item
        PgUp/PgDn [K/J] : go to previous/next item of same type
 Right/Left-arrow [l/h] : go to child item / parent item
                      f : fold / unfold item, hiding/revealing its children
                      F : fold parent item
                      c : commit selected changes
                      q : quit without committing (no changes will be made)
                      ? : help (what you're currently reading)"""
        
        helpwin = curses.newwin(self.yScreenSize,0,0,0)
        helpLines = helpText.split("\n")
        helpLines = helpLines + [" "]*(self.yScreenSize-self.numStatusLines-len(helpLines)-1)
        try:
            for line in helpLines:
                self.printString(helpwin, self.alignString(line), pairName="legend")
        except curses.error:
            pass
        helpwin.refresh()
        self.stdscr.getch()

    def confirmCommit(self):
        "Ask for 'Y' to be pressed to confirm commit. Return True if confirmed."
        confirmText = "Are you sure you want to commit the selected changes [yN]? "
        
        confirmWin = curses.newwin(self.yScreenSize,0,0,0)
        try:
            self.printString(confirmWin, self.alignString(confirmText), pairName="selected")
        except curses.error:
            pass
        confirmWin.refresh()
        try:
            response = chr(self.stdscr.getch())
        except ValueError:
            response = "n"
        if response.lower().startswith("y"):
            return True
        else:
            return False

    def main(self, stdscr):
        """
        Method to be wrapped by curses.wrapper() for selecting chunks.
        
        """
        signal.signal(signal.SIGWINCH, self.sigwinchHandler)
        self.stdscr = stdscr
        self.yScreenSize, self.xScreenSize = self.stdscr.getmaxyx()
        
        # available colors: black, blue, cyan, green, magenta, white, yellow
        # init_pair(color_id, foreground_color, background_color)
        self.initColorPair(curses.COLOR_WHITE, curses.COLOR_BLACK, name="normal")
        self.initColorPair(curses.COLOR_WHITE, curses.COLOR_RED, name="selected")
        self.initColorPair(curses.COLOR_RED, curses.COLOR_BLACK, name="deletion")
        self.initColorPair(curses.COLOR_GREEN, curses.COLOR_BLACK, name="addition")
        self.initColorPair(curses.COLOR_WHITE, curses.COLOR_BLUE, name="legend")
        # newwin([height, width,] begin_y, begin_x)
        self.statuswin = curses.newwin(self.numStatusLines,0,0,0)
        
        # figure out how much space to allocate for the chunk-pad which is
        # used for displaying the patch
        
        # stupid hack to prevent getNumLinesDisplayed from failing
        self.chunkpad = curses.newpad(1,self.xScreenSize)
        
        self.numPadLines = self.getNumLinesDisplayed()
        self.chunkpad = curses.newpad(self.numPadLines, self.xScreenSize)
        
        # initialize selecteItemEndLine (initial start-line is 0)
        self.selectedItemEndLine = self.getNumLinesDisplayed(self.currentSelectedItem, recurseChildren=False)
        
        #import rpdb2; rpdb2.start_embedded_debugger("secret")

        while True:
            self.updateScreen()
            self.lastKeyPressed = keyPressed = stdscr.getch()
            if keyPressed in [ord("k"), curses.KEY_UP]:
                self.upArrowEvent()
            if keyPressed in [ord("K"), curses.KEY_PPAGE]:
                self.upArrowShiftEvent()
            elif keyPressed in [ord("j"), curses.KEY_DOWN]:
                self.downArrowEvent()
            elif keyPressed in [ord("J"), curses.KEY_NPAGE]:
                self.downArrowShiftEvent()
            elif keyPressed in [ord("l"), curses.KEY_RIGHT]:
                self.rightArrowEvent()
            elif keyPressed in [ord("h"), curses.KEY_LEFT]:
                self.leftArrowEvent()
            elif keyPressed in [ord("q")]:
                raise util.Abort(_('user quit'))
            elif keyPressed in [ord("c")]:
                if self.confirmCommit():
                    break
            elif keyPressed in [ord(' ')]:
                self.toggleApply()
            elif keyPressed in [ord("f")]:
                self.toggleFolded()
            elif keyPressed in [ord("F")]:
                self.toggleFolded(foldParent=True)
            elif keyPressed in [ord("?")]:
                self.helpWindow()

def crecord(ui, repo, *pats, **opts):
    '''interactively select changes to commit

    If a list of files is omitted, all changes reported by "hg status"
    will be candidates for recording.

    See 'hg help dates' for a list of formats valid for -d/--date.

    You will be shown a list of patch hunks from which you can select
    those you would like to apply to the commit.
    
    '''
    def record_committer(ui, repo, pats, opts):
        commands.commit(ui, repo, *pats, **opts)

    dorecord(ui, repo, record_committer, *pats, **opts)


def qcrecord(ui, repo, patch, *pats, **opts):
    '''interactively record a new patch

    see 'hg help qnew' & 'hg help record' for more information and usage
    '''

    try:
        mq = extensions.find('mq')
    except KeyError:
        raise util.Abort(_("'mq' extension not loaded"))

    def qrecord_committer(ui, repo, pats, opts):
        mq.new(ui, repo, patch, *pats, **opts)

    opts = opts.copy()
    opts['force'] = True    # always 'qnew -f'
    dorecord(ui, repo, qrecord_committer, *pats, **opts)


def dorecord(ui, repo, committer, *pats, **opts):
    if not ui.interactive:
        raise util.Abort(_('running non-interactively, use commit instead'))

    def recordfunc(ui, repo, message, match, opts):
        """This is generic record driver.

        It's job is to interactively filter local changes, and accordingly
        prepare working dir into a state, where the job can be delegated to
        non-interactive commit command such as 'commit' or 'qrefresh'.

        After the actual job is done by non-interactive command, working dir
        state is restored to original.

        In the end we'll record intresting changes, and everything else will be
        left in place, so the user can continue his work.
        """
        if match.files():
            changes = None
        else:
            changes = repo.status(match=match)[:3]
            modified, added, removed = changes
            match = cmdutil.matchfiles(repo, modified + added + removed)
        diffopts = mdiff.diffopts(git=True, nodates=True)
        chunks = patch.diff(repo, repo.dirstate.parents()[0], match=match,
                            changes=changes, opts=diffopts)
        fp = cStringIO.StringIO()
        fp.write(''.join(chunks))
        fp.seek(0)

        # 1. filter patch, so we have intending-to apply subset of it
        chunks = filterpatch(ui, parsepatch(fp))
        del fp

        contenders = {}
        for h in chunks:
            try: contenders.update(dict.fromkeys(h.files()))
            except AttributeError: pass

        newfiles = [f for f in match.files() if f in contenders]

        if not newfiles:
            ui.status(_('no changes to record\n'))
            return 0

        if changes is None:
            match = cmdutil.matchfiles(repo, newfiles)
            changes = repo.status(match=match)
        modified = dict.fromkeys(changes[0])

        # 2. backup changed files, so we can restore them in the end
        backups = {}
        backupdir = repo.join('record-backups')
        try:
            os.mkdir(backupdir)
        except OSError, err:
            if err.errno != errno.EEXIST:
                raise
        try:
            # backup continues
            for f in newfiles:
                if f not in modified:
                    continue
                fd, tmpname = tempfile.mkstemp(prefix=f.replace('/', '_')+'.',
                                               dir=backupdir)
                os.close(fd)
                ui.debug(_('backup %r as %r\n') % (f, tmpname))
                util.copyfile(repo.wjoin(f), tmpname)
                backups[f] = tmpname

            fp = cStringIO.StringIO()
            for c in chunks:
                if c.filename() in backups:
                    c.write(fp)
            dopatch = fp.tell()
            fp.seek(0)

            # 3a. apply filtered patch to clean repo  (clean)
            if backups:
                hg.revert(repo, repo.dirstate.parents()[0], backups.has_key)

            # 3b. (apply)
            if dopatch:
                try:
                    ui.debug(_('applying patch\n'))
                    ui.debug(fp.getvalue())
                    patch.internalpatch(fp, ui, 1, repo.root)
                except patch.PatchError, err:
                    s = str(err)
                    if s:
                        raise util.Abort(s)
                    else:
                        raise util.Abort(_('patch failed to apply'))
            del fp

            # 4. We prepared working directory according to filtered patch.
            #    Now is the time to delegate the job to commit/qrefresh or the like!

            # it is important to first chdir to repo root -- we'll call a
            # highlevel command with list of pathnames relative to repo root
            cwd = os.getcwd()
            os.chdir(repo.root)
            try:
                committer(ui, repo, newfiles, opts)
            finally:
                os.chdir(cwd)

            return 0
        finally:
            # 5. finally restore backed-up files
            try:
                for realname, tmpname in backups.iteritems():
                    ui.debug(_('restoring %r to %r\n') % (tmpname, realname))
                    util.copyfile(tmpname, repo.wjoin(realname))
                    os.unlink(tmpname)
                os.rmdir(backupdir)
            except OSError:
                pass
    return cmdutil.commit(ui, repo, recordfunc, pats, opts)

cmdtable = {
    "crecord":
        (crecord,

         # add commit options
         commands.table['^commit|ci'][1],

         _('hg crecord [OPTION]... [FILE]...')),
}


def extsetup():
    try:
        mq = extensions.find('mq')
    except KeyError:
        return

    qcmdtable = {
    "qcrecord":
        (qcrecord,

         # add qnew options, except '--force'
         [opt for opt in mq.cmdtable['qnew'][1] if opt[1] != 'force'],

         _('hg qcrecord [OPTION]... PATCH [FILE]...')),
    }

    cmdtable.update(qcmdtable)

