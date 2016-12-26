from gettext import gettext as _
from . import util

from . import encoding
code = encoding.encoding

import os
import re
import sys
import fcntl
import struct
import termios
import signal

# This is required for ncurses to display non-ASCII characters in default user
# locale encoding correctly.  --immerrr
import locale
locale.setlocale(locale.LC_ALL, u'')

from .crpatch import patch, uiheader, uihunk, uihunkline

# os.name is one of: 'posix', 'nt', 'dos', 'os2', 'mac', or 'ce'
if os.name == 'posix':
    import curses
else:
    # I have no idea if wcurses works with crecord...
    import wcurses as curses

try:
    curses
except NameError:
    raise util.Abort(
        _('the python curses/wcurses module is not available/installed'))


_origstdout = sys.__stdout__ # used by gethw()

def gethw():
    """
    Magically get the current height and width of the window (without initscr)

    This is a rip-off of a rip-off - taken from the bpython code.  It is
    useful / necessary because otherwise curses.initscr() must be called,
    which can leave the terminal in a nasty state after exiting.

    """
    h, w = struct.unpack(
        "hhhh", fcntl.ioctl(_origstdout, termios.TIOCGWINSZ, "\000"*8))[0:2]
    return h, w


def chunkselector(opts, headerlist, ui):
    """
    Curses interface to get selection of chunks, and mark the applied flags
    of the chosen chunks.

    """
    chunkselector = CursesChunkSelector(headerlist, ui)
    curses.wrapper(chunkselector.main, opts)

class CursesChunkSelector(object):
    def __init__(self, headerlist, ui):
        # put the headers into a patch object
        self.headerlist = patch(headerlist)

        self.ui = ui

        # list of all chunks
        self.chunklist = []
        for h in headerlist:
            self.chunklist.append(h)
            self.chunklist.extend(h.hunks)

        # dictionary mapping (fgcolor, bgcolor) pairs to the
        # corresponding curses color-pair value.
        self.colorpairs = {}
        # maps custom nicknames of color-pairs to curses color-pair values
        self.colorpairnames = {}

        # the currently selected header, hunk, or hunk-line
        self.currentselecteditem = self.headerlist[0]

        # updated when printing out patch-display -- the 'lines' here are the
        # line positions *in the pad*, not on the screen.
        self.selecteditemstartline = 0
        self.selecteditemendline = None

        # define indentation levels
        self.headerindentnumchars = 0
        self.hunkindentnumchars = 3
        self.hunklineindentnumchars = 6

        # the first line of the pad to print to the screen
        self.firstlineofpadtoprint = 0

        # keeps track of the number of lines in the pad
        self.numpadlines = None

        self.numstatuslines = 2

        # keep a running count of the number of lines printed to the pad
        # (used for determining when the selected item begins/ends)
        self.linesprintedtopadsofar = 0

        # the first line of the pad which is visible on the screen
        self.firstlineofpadtoprint = 0

        # if the last 'toggle all' command caused all changes to be applied
        self.waslasttoggleallapplied = True

    def uparrowevent(self):
        """
        Try to select the previous item to the current item that has the
        most-indented level.  For example, if a hunk is selected, try to select
        the last HunkLine of the hunk prior to the selected hunk.  Or, if
        the first HunkLine of a hunk is currently selected, then select the
        hunk itself.
        """
        currentitem = self.currentselecteditem

        nextitem = currentitem.previtem()

        if nextitem is None:
            # if no parent item (i.e. currentitem is the first header), then
            # no change...
            nextitem = currentitem

        self.currentselecteditem = nextitem

    def uparrowshiftevent(self):
        """
        Select (if possible) the previous item on the same level as the
        currently selected item.  Otherwise, select (if possible) the
        parent-item of the currently selected item.
        """
        currentitem = self.currentselecteditem
        nextitem = currentitem.prevsibling()
        # if there's no previous sibling, try choosing the parent
        if nextitem is None:
            nextitem = currentitem.parentitem()
        if nextitem is None:
            # if no parent item (i.e. currentitem is the first header), then
            # no change...
            nextitem = currentitem

        self.currentselecteditem = nextitem

    def downarrowevent(self):
        """
        Try to select the next item to the current item that has the
        most-indented level.  For example, if a hunk is selected, select
        the first HunkLine of the selected hunk.  Or, if the last HunkLine of
        a hunk is currently selected, then select the next hunk, if one exists,
        or if not, the next header if one exists.
        """
        #self.startprintline += 1 #debug
        currentitem = self.currentselecteditem

        nextitem = currentitem.nextitem()
        # if there's no next item, keep the selection as-is
        if nextitem is None:
            nextitem = currentitem

        self.currentselecteditem = nextitem

    def downarrowshiftevent(self):
        """
        Select (if possible) the next item on the same level as the currently
        selected item.  Otherwise, select (if possible) the next item on the
        same level as the parent item of the currently selected item.
        """
        currentitem = self.currentselecteditem
        nextitem = currentitem.nextsibling()
        # if there's no next sibling, try choosing the parent's nextsibling
        if nextitem is None:
            try:
                nextitem = currentitem.parentitem().nextsibling()
            except AttributeError:
                # parentitem returned None, so nextsibling() can't be called
                nextitem = None
        if nextitem is None:
            # if parent has no next sibling, then no change...
            nextitem = currentitem

        self.currentselecteditem = nextitem

    def rightarrowevent(self):
        """
        Select (if possible) the first of this item's child-items.

        """
        currentitem = self.currentselecteditem
        nextitem = currentitem.firstchild()

        # turn off folding if we want to show a child-item
        if currentitem.folded:
            self.togglefolded(currentitem)

        if nextitem is None:
            # if no next item on parent-level, then no change...
            nextitem = currentitem

        self.currentselecteditem = nextitem

    def leftarrowevent(self):
        """
        If the current item can be folded (i.e. it is an unfolded header or
        hunk), then fold it.  Otherwise try select (if possible) the parent
        of this item.

        """
        currentitem = self.currentselecteditem

        # try to fold the item
        if not isinstance(currentitem, uihunkline):
            if not currentitem.folded:
                self.togglefolded(item=currentitem)
                return

        # if it can't be folded, try to select the parent item
        nextitem = currentitem.parentitem()

        if nextitem is None:
            # if no item on parent-level, then no change...
            nextitem = currentitem
            if not nextitem.folded:
                self.togglefolded(item=nextitem)

        self.currentselecteditem = nextitem

    def leftarrowshiftevent(self):
        """
        Select the header of the current item (or fold current item if the
        current item is already a header).

        """
        currentitem = self.currentselecteditem

        if isinstance(currentitem, uiheader):
            if not currentitem.folded:
                self.togglefolded(item=currentitem)
                return

        # select the parent item recursively until we're at a header
        while True:
            nextitem = currentitem.parentitem()
            if nextitem is None:
                break
            else:
                currentitem = nextitem

        self.currentselecteditem = currentitem

    def updatescroll(self):
        "Scroll the screen to fully show the currently-selected"
        selstart = self.selecteditemstartline
        selend = self.selecteditemendline
        #selnumlines = selend - selstart
        padstart = self.firstlineofpadtoprint
        padend = padstart + self.yscreensize - self.numstatuslines - 1
        # 'buffered' pad start/end values which scroll with a certain
        # top/bottom context margin
        padstartbuffered = padstart + 3
        padendbuffered = padend - 3

        if selend > padendbuffered:
            self.scrolllines(selend - padendbuffered)
        elif selstart < padstartbuffered:
            # negative values scroll in pgup direction
            self.scrolllines(selstart - padstartbuffered)


    def scrolllines(self, numlines):
        "Scroll the screen up (down) by numlines when numlines >0 (<0)."
        self.firstlineofpadtoprint += numlines
        if self.firstlineofpadtoprint < 0:
            self.firstlineofpadtoprint = 0
        if self.firstlineofpadtoprint > self.numpadlines - 1:
            self.firstlineofpadtoprint = self.numpadlines - 1

    def toggleapply(self, item=None):
        """
        Toggle the applied flag of the specified item.  If no item is specified,
        toggle the flag of the currently selected item.

        """
        if item is None:
            item = self.currentselecteditem

        item.applied = not item.applied

        if isinstance(item, uiheader):
            item.partial = False
            if item.applied:
                # apply all its hunks
                for hnk in item.hunks:
                    hnk.applied = True
                    # apply all their hunklines
                    for hunkline in hnk.changedlines:
                        hunkline.applied = True
            else:
                # un-apply all its hunks
                for hnk in item.hunks:
                    hnk.applied = False
                    hnk.partial = False
                    # un-apply all their hunklines
                    for hunkline in hnk.changedlines:
                        hunkline.applied = False
        elif isinstance(item, uihunk):
            item.partial = False
            # apply all it's hunklines
            for hunkline in item.changedlines:
                hunkline.applied = item.applied

            siblingappliedstatus = [hnk.applied for hnk in item.header.hunks]
            allsiblingsapplied = not (False in siblingappliedstatus)
            nosiblingsapplied = not (True in siblingappliedstatus)

            siblingspartialstatus = [hnk.partial for hnk in item.header.hunks]
            somesiblingspartial = (True in siblingspartialstatus)

            #cases where applied or partial should be removed from header

            # if no 'sibling' hunks are applied (including this hunk)
            if nosiblingsapplied:
                if not item.header.special():
                    item.header.applied = False
                    item.header.partial = False
            else: # some/all parent siblings are applied
                item.header.applied = True
                item.header.partial = (somesiblingspartial or
                                        not allsiblingsapplied)

        elif isinstance(item, uihunkline):
            siblingappliedstatus = [ln.applied for ln in item.hunk.changedlines]
            allsiblingsapplied = not (False in siblingappliedstatus)
            nosiblingsapplied = not (True in siblingappliedstatus)

            # if no 'sibling' lines are applied
            if nosiblingsapplied:
                item.hunk.applied = False
                item.hunk.partial = False
            elif allsiblingsapplied:
                item.hunk.applied = True
                item.hunk.partial = False
            else: # some siblings applied
                item.hunk.applied = True
                item.hunk.partial = True

            parentsiblingsapplied = [hnk.applied for hnk
                                     in item.hunk.header.hunks]
            noparentsiblingsapplied = not (True in parentsiblingsapplied)
            allparentsiblingsapplied = not (False in parentsiblingsapplied)

            parentsiblingspartial = [hnk.partial for hnk
                                     in item.hunk.header.hunks]
            someparentsiblingspartial = (True in parentsiblingspartial)

            # if all parent hunks are not applied, un-apply header
            if noparentsiblingsapplied:
                if not item.hunk.header.special():
                    item.hunk.header.applied = False
                    item.hunk.header.partial = False
            # set the applied and partial status of the header if needed
            else: # some/all parent siblings are applied
                item.hunk.header.applied = True
                item.hunk.header.partial = (someparentsiblingspartial or
                                            not allparentsiblingsapplied)

    def toggleall(self):
        "Toggle the applied flag of all items."
        if self.waslasttoggleallapplied: # then unapply them this time
            for item in self.headerlist:
                if item.applied:
                    self.toggleapply(item)
        else:
            for item in self.headerlist:
                if not item.applied:
                    self.toggleapply(item)
        self.waslasttoggleallapplied = not self.waslasttoggleallapplied

    def togglefolded(self, item=None, foldparent=False):
        "Toggle folded flag of specified item (defaults to currently selected)"
        if item is None:
            item = self.currentselecteditem
        if foldparent or (isinstance(item, uiheader) and item.neverunfolded):
            if not isinstance(item, uiheader):
                # we need to select the parent item in this case
                self.currentselecteditem = item = item.parentitem()
            elif item.neverunfolded:
                item.neverunfolded = False

            # also fold any foldable children of the parent/current item
            if isinstance(item, uiheader): # the original OR 'new' item
                for child in item.allchildren():
                    child.folded = not item.folded

        if isinstance(item, (uiheader, uihunk)):
            item.folded = not item.folded


    def alignstring(self, instr, window):
        """
        Add whitespace to the end of a string in order to make it fill
        the screen in the x direction.  The current cursor position is
        taken into account when making this calculation.  The string can span
        multiple lines.

        """
        y, xstart = window.getyx()
        width = self.xscreensize
        # turn tabs into spaces
        instr = instr.expandtabs(4)
        try:
            strlen = len(unicode(encoding.fromlocal(instr), code))
        except Exception:
            # if text is not utf8, then assume an 8-bit single-byte encoding.
            strlen = len(instr)

        numspaces = (width - ((strlen + xstart) % width) - 1)
        return instr + " " * numspaces + "\n"

    def printstring(self, window, text, fgcolor=None, bgcolor=None, pair=None,
        pairname=None, attrlist=None, towin=True, align=True, showwhtspc=False):
        """
        Print the string, text, with the specified colors and attributes, to
        the specified curses window object.

        The foreground and background colors are of the form
        curses.COLOR_XXXX, where XXXX is one of: [BLACK, BLUE, CYAN, GREEN,
        MAGENTA, RED, WHITE, YELLOW].  If pairname is provided, a color
        pair will be looked up in the self.colorpairnames dictionary.

        attrlist is a list containing text attributes in the form of
        curses.A_XXXX, where XXXX can be: [BOLD, DIM, NORMAL, STANDOUT,
        UNDERLINE].

        If align == True, whitespace is added to the printed string such that
        the string stretches to the right border of the window.

        If showwhtspc == True, trailing whitespace of a string is highlighted.

        """
        # preprocess the text, converting tabs to spaces
        text = text.expandtabs(4)
        # Strip \n, and convert control characters to ^[char] representation
        text = re.sub(r'[\x00-\x08\x0a-\x1f]',
                lambda m:'^' + chr(ord(m.group()) + 64), text.strip('\n'))

        if pair is not None:
            colorpair = pair
        elif pairname is not None:
            colorpair = self.colorpairnames[pairname]
        else:
            if fgcolor is None:
                fgcolor = -1
            if bgcolor is None:
                bgcolor = -1
            if (fgcolor, bgcolor) in self.colorpairs:
                colorpair = self.colorpairs[(fgcolor, bgcolor)]
            else:
                colorpair = self.getcolorpair(fgcolor, bgcolor)
        # add attributes if possible
        if attrlist is None:
            attrlist = []
        if colorpair < 256:
            # then it is safe to apply all attributes
            for textattr in attrlist:
                colorpair |= textattr
        else:
            # just apply a select few (safe?) attributes
            for textattr in (curses.A_UNDERLINE, curses.A_BOLD):
                if textattr in attrlist:
                    colorpair |= textattr

        y, xstart = self.chunkpad.getyx()
        t = "" # variable for counting lines printed
        # if requested, show trailing whitespace
        if showwhtspc:
            origlen = len(text)
            text = text.rstrip(' \n') # tabs have already been expanded
            strippedlen = len(text)
            numtrailingspaces = origlen - strippedlen

        if towin:
            window.addstr(text, colorpair)
        t += text

        if showwhtspc:
                wscolorpair = colorpair | curses.A_REVERSE
                if towin:
                    for i in range(numtrailingspaces):
                        window.addch(curses.ACS_CKBOARD, wscolorpair)
                t += " " * numtrailingspaces

        if align:
            if towin:
                extrawhitespace = self.alignstring("", window)
                window.addstr(extrawhitespace, colorpair)
            else:
                # need to use t, since the x position hasn't incremented
                extrawhitespace = self.alignstring(t, window)
            t += extrawhitespace

        # is reset to 0 at the beginning of printitem()

        linesprinted = (xstart + len(t)) / self.xscreensize
        self.linesprintedtopadsofar += linesprinted
        return t

    def updatescreen(self):
        self.statuswin.erase()
        self.chunkpad.erase()

        printstring = self.printstring

        # print out the status lines at the top
        try:
            printstring(self.statuswin,
                        "SELECT CHUNKS: (j/k/up/dn/pgup/pgdn) move cursor; "
                        "(space/A) toggle hunk/all; (f)old/unfold",
                        pairname="legend")
            printstring(self.statuswin,
                        " (c)ommit/(s)tage applied; (q)uit; (?) help;"
                        "toggle (a)mend mode | [x]=hunk applied **=folded",
                        pairname="legend")
        except curses.error:
            pass

        # print out the patch in the remaining part of the window
        try:
            self.printitem()
            self.updatescroll()
            self.chunkpad.refresh(self.firstlineofpadtoprint, 0,
                                  self.numstatuslines, 0,
                                  self.yscreensize + 1 - self.numstatuslines,
                                  self.xscreensize)
        except curses.error:
            pass

        # refresh([pminrow, pmincol, sminrow, smincol, smaxrow, smaxcol])
        self.statuswin.refresh()

    def getstatusprefixstring(self, item):
        """
        Create a string to prefix a line with which indicates whether 'item'
        is applied and/or folded.

        """
        # create checkbox string
        if item.applied:
            if not isinstance(item, uihunkline) and item.partial:
                checkbox = "[~]"
            else:
                checkbox = "[x]"
        else:
            checkbox = "[ ]"

        try:
            if item.folded:
                checkbox += "**"
                if isinstance(item, uiheader):
                    # one of "M", "A", or "D" (modified, added, deleted)
                    filestatus = item.changetype

                    checkbox += filestatus + " "
            else:
                checkbox += "  "
                if isinstance(item, uiheader):
                    # add two more spaces for headers
                    checkbox += "  "
        except AttributeError: # not foldable
            checkbox += "  "

        return checkbox

    def printheader(self, header, selected=False, towin=True,
                    ignorefolding=False):
        """
        Print the header to the pad.  If countLines is True, don't print
        anything, but just count the number of lines which would be printed.

        """
        outstr = ""
        text = header.prettystr()
        chunkindex = self.chunklist.index(header)

        if chunkindex != 0 and not header.folded:
            # add separating line before headers
            outstr += self.printstring(self.chunkpad, '_' * self.xscreensize,
                                       towin=towin, align=False)
        # select color-pair based on if the header is selected
        colorpair = self.getcolorpair(name=selected and "selected" or "normal",
                                      attrlist=[curses.A_BOLD])

        # print out each line of the chunk, expanding it to screen width

        # number of characters to indent lines on this level by
        indentnumchars = 0
        checkbox = self.getstatusprefixstring(header)
        if not header.folded or ignorefolding:
            textlist = text.split("\n")
            linestr = checkbox + textlist[0]
        else:
            linestr = checkbox + header.filename()
        outstr += self.printstring(self.chunkpad, linestr, pair=colorpair,
                                   towin=towin)
        if not header.folded or ignorefolding:
            if len(textlist) > 1:
                for line in textlist[1:]:
                    linestr = " "*(indentnumchars + len(checkbox)) + line
                    outstr += self.printstring(self.chunkpad, linestr,
                                               pair=colorpair, towin=towin)

        return outstr

    def printhunklinesbefore(self, hunk, selected=False, towin=True,
                             ignorefolding=False):
        "includes start/end line indicator"
        outstr = ""
        # where hunk is in list of siblings
        hunkindex = hunk.header.hunks.index(hunk)

        if hunkindex != 0:
            # add separating line before headers
            outstr += self.printstring(self.chunkpad, ' '*self.xscreensize,
                                       towin=towin, align=False)

        colorpair = self.getcolorpair(name=selected and "selected" or "normal",
                                      attrlist=[curses.A_BOLD])

        # print out from-to line with checkbox
        checkbox = self.getstatusprefixstring(hunk)

        lineprefix = " "*self.hunkindentnumchars + checkbox
        frtoline = "   " + hunk.getfromtoline().strip("\n")


        outstr += self.printstring(self.chunkpad, lineprefix, towin=towin,
                                   align=False) # add uncolored checkbox/indent
        outstr += self.printstring(self.chunkpad, frtoline, pair=colorpair,
                                   towin=towin)

        if hunk.folded and not ignorefolding:
            # skip remainder of output
            return outstr

        # print out lines of the chunk preceeding changed-lines
        for line in hunk.before:
            linestr = " "*(self.hunklineindentnumchars + len(checkbox)) + line
            outstr += self.printstring(self.chunkpad, linestr, towin=towin)

        return outstr

    def printhunklinesafter(self, hunk, towin=True, ignorefolding=False):
        outstr = ""
        if hunk.folded and not ignorefolding:
            return outstr

        # a bit superfluous, but to avoid hard-coding indent amount
        checkbox = self.getstatusprefixstring(hunk)
        for line in hunk.after:
            linestr = " "*(self.hunklineindentnumchars + len(checkbox)) + line
            outstr += self.printstring(self.chunkpad, linestr, towin=towin)

        return outstr

    def printhunkchangedline(self, hunkline, selected=False, towin=True):
        outstr = ""
        checkbox = self.getstatusprefixstring(hunkline)

        linestr = hunkline.prettystr().strip("\n")

        # select color-pair based on whether line is an addition/removal
        if selected:
            colorpair = self.getcolorpair(name="selected")
        elif linestr.startswith("+"):
            colorpair = self.getcolorpair(name="addition")
        elif linestr.startswith("-"):
            colorpair = self.getcolorpair(name="deletion")
        elif linestr.startswith("\\"):
            colorpair = self.getcolorpair(name="normal")

        lineprefix = " "*self.hunklineindentnumchars + checkbox
        outstr += self.printstring(self.chunkpad, lineprefix, towin=towin,
                                   align=False) # add uncolored checkbox/indent
        outstr += self.printstring(self.chunkpad, linestr, pair=colorpair,
                                   towin=towin, showwhtspc=True)
        return outstr

    def printitem(self, item=None, ignorefolding=False, recursechildren=True,
                  towin=True):
        """
        Use __printitem() to print the the specified item.applied.
        If item is not specified, then print the entire patch.
        (hiding folded elements, etc. -- see __printitem() docstring)
        """
        if item is None:
            item = self.headerlist
        if recursechildren:
            self.linesprintedtopadsofar = 0

        outstr = []
        self.__printitem(item, ignorefolding, recursechildren, outstr,
                                  towin=towin)
        return ''.join(outstr)

    def outofdisplayedarea(self):
        y, _ = self.chunkpad.getyx() # cursor location
        # * 2 here works but an optimization would be the max number of
        # consecutive non selectable lines
        # i.e the max number of context line for any hunk in the patch
        miny = min(0, self.firstlineofpadtoprint - self.yscreensize)
        maxy = self.firstlineofpadtoprint + self.yscreensize * 2
        return y < miny or y > maxy

    def handleselection(self, item, recursechildren):
        selected = (item is self.currentselecteditem)
        if selected and recursechildren:
            # assumes line numbering starting from line 0
            self.selecteditemstartline = self.linesprintedtopadsofar
            selecteditemlines = self.getnumlinesdisplayed(item,
                                                          recursechildren=False)
            self.selecteditemendline = (self.selecteditemstartline +
                                        selecteditemlines - 1)
        return selected

    def __printitem(self, item, ignorefolding, recursechildren, outstr,
                    towin=True):
        """
        Recursive method for printing out patch/header/hunk/hunk-line data to
        screen.  Also returns a string with all of the content of the displayed
        patch (not including coloring, etc.).

        If ignorefolding is True, then folded items are printed out.

        If recursechildren is False, then only print the item without its
        child items.

        """
        if towin and self.outofdisplayedarea():
            return

        selected = self.handleselection(item, recursechildren)

        # Patch object is a list of headers
        if isinstance(item, patch):
            if recursechildren:
                for hdr in item:
                    self.__printitem(hdr, ignorefolding,
                            recursechildren, outstr, towin)
        # TODO: eliminate all isinstance() calls
        if isinstance(item, uiheader):
            outstr.append(self.printheader(item, selected, towin=towin,
                                       ignorefolding=ignorefolding))
            if recursechildren:
                for hnk in item.hunks:
                    self.__printitem(hnk, ignorefolding,
                            recursechildren, outstr, towin)
        elif (isinstance(item, uihunk) and
              ((not item.header.folded) or ignorefolding)):
            # print the hunk data which comes before the changed-lines
            outstr.append(self.printhunklinesbefore(item, selected, towin=towin,
                                                ignorefolding=ignorefolding))
            if recursechildren:
                for l in item.changedlines:
                    self.__printitem(l, ignorefolding,
                            recursechildren, outstr, towin)
                outstr.append(self.printhunklinesafter(item, towin=towin,
                                                ignorefolding=ignorefolding))
        elif (isinstance(item, uihunkline) and
              ((not item.hunk.folded) or ignorefolding)):
            outstr.append(self.printhunkchangedline(item, selected,
                towin=towin))

        return outstr

    def getnumlinesdisplayed(self, item=None, ignorefolding=False,
                             recursechildren=True):
        """
        Return the number of lines which would be displayed if the item were
        to be printed to the display.  The item will NOT be printed to the
        display (pad).
        If no item is given, assume the entire patch.
        If ignorefolding is True, folded items will be unfolded when counting
        the number of lines.

        """
        # temporarily disable printing to windows by printstring
        patchdisplaystring = self.printitem(item, ignorefolding,
                                            recursechildren, towin=False)
        numlines = len(patchdisplaystring) / self.xscreensize
        return numlines

    def sigwinchhandler(self, n, frame):
        "Handle window resizing"
        try:
            curses.endwin()
            self.yscreensize, self.xscreensize = gethw()
            self.statuswin.resize(self.numstatuslines, self.xscreensize)
            self.numpadlines = self.getnumlinesdisplayed(ignorefolding=True) + 1
            self.chunkpad = curses.newpad(self.numpadlines, self.xscreensize)
        except curses.error:
            pass

    def getcolorpair(self, fgcolor=None, bgcolor=None, name=None,
                     attrlist=None):
        """
        Get a curses color pair, adding it to self.colorPairs if it is not
        already defined.  An optional string, name, can be passed as a shortcut
        for referring to the color-pair.  By default, if no arguments are
        specified, the white foreground / black background color-pair is
        returned.

        It is expected that this function will be used exclusively for
        initializing color pairs, and NOT curses.init_pair().

        attrlist is used to 'flavor' the returned color-pair.  This information
        is not stored in self.colorpairs.  It contains attribute values like
        curses.A_BOLD.

        """
        if (name is not None) and name in self.colorpairnames:
            # then get the associated color pair and return it
            colorpair = self.colorpairnames[name]
        else:
            if fgcolor is None:
                fgcolor = -1
            if bgcolor is None:
                bgcolor = -1
            if (fgcolor, bgcolor) in self.colorpairs:
                colorpair = self.colorpairs[(fgcolor, bgcolor)]
            else:
                pairindex = len(self.colorpairs) + 1
                curses.init_pair(pairindex, fgcolor, bgcolor)
                colorpair = self.colorpairs[(fgcolor, bgcolor)] = (
                    curses.color_pair(pairindex))
                if name is not None:
                    self.colorpairnames[name] = curses.color_pair(pairindex)

        # add attributes if possible
        if attrlist is None:
            attrlist = []
        if colorpair < 256:
            # then it is safe to apply all attributes
            for textattr in attrlist:
                colorpair |= textattr
        else:
            # just apply a select few (safe?) attributes
            for textattrib in (curses.A_UNDERLINE, curses.A_BOLD):
                if textattrib in attrlist:
                    colorpair |= textattrib
        return colorpair

    def initcolorpair(self, *args, **kwargs):
        "Same as getcolorpair."
        self.getcolorpair(*args, **kwargs)

    def helpwindow(self):
        "Print a help window to the screen.  Exit after any keypress."
        helptext = """            [press any key to return to the patch-display]

crecord allows you to interactively choose among the changes you have made,
and confirm only those changes you select for further processing by the command
you are running (commit/stage/unstage), after confirming the selected
changes, the unselected changes are still present in your working copy, so you
can use crecord multiple times to split large changes into smaller changesets.
The following are valid keystrokes:

                [SPACE] : (un-)select item ([~]/[X] = partly/fully applied)
                      A : (un-)select all items
    Up/Down-arrow [k/j] : go to previous/next unfolded item
        PgUp/PgDn [K/J] : go to previous/next item of same type
 Right/Left-arrow [l/h] : go to child item / parent item
 Shift-Left-arrow   [H] : go to parent header / fold selected header
                      f : fold / unfold item, hiding/revealing its children
                      F : fold / unfold parent item and all of its ancestors
                 ctrl-l : scroll the selected line to the top of the screen
                      a : toggle amend mode
                      c : commit selected changes
                      s : stage selected changes
                      r : review/edit and commit selected changes
                      q : quit without committing (no changes will be made)
                      ? : help (what you're currently reading)"""

        helpwin = curses.newwin(self.yscreensize, 0, 0, 0)
        helplines = helptext.split("\n")
        helplines = helplines + [" "]*(
            self.yscreensize - self.numstatuslines - len(helplines) - 1)
        try:
            for line in helplines:
                self.printstring(helpwin, line, pairname="legend")
        except curses.error:
            pass
        helpwin.refresh()
        try:
            helpwin.getkey()
        except curses.error:
            pass

    def commitmessagewindow(self, commenttext):
        "Create a temporary commit message editing window on the screen."
            
        curses.raw()
        curses.def_prog_mode()
        curses.endwin()
        commenttext = self.ui.edit(commenttext, self.ui.username(),
                                   name=os.path.join(self.ui.repo.controldir(),
                                                     'COMMIT_EDITMSG'))
        curses.cbreak()
        self.stdscr.refresh()
        self.stdscr.keypad(1) # allow arrow-keys to continue to function
        return commenttext

    def confirmationwindow(self, windowtext):
        "Display an informational window, then wait for and return a keypress."

        confirmwin = curses.newwin(self.yscreensize, 0, 0, 0)
        try:
            lines = windowtext.split("\n")
            for line in lines:
                self.printstring(confirmwin, line, pairname="selected")
        except curses.error:
            pass
        self.stdscr.refresh()
        confirmwin.refresh()
        try:
            response = chr(self.stdscr.getch())
        except ValueError:
            response = None

        return response

    def confirmcommit(self, review=False, stage=False):
        """Ask for 'Y' to be pressed to confirm selected. Return True if
        confirmed."""
        if review:
            confirmtext = (
"""If you answer yes to the following, the your currently chosen patch chunks
will be loaded into an editor.  You may modify the patch from the editor, and
save the changes if you wish to change the patch.  Otherwise, you can just
close the editor without saving to accept the current patch as-is.

NOTE: don't add/remove lines unless you also modify the range information.
      Failing to follow this rule will result in the commit aborting.

Are you sure you want to review/edit and confirm the selected changes [yN]?
""")
        elif stage:
            confirmtext = (
                "Are you sure you want to stage the selected changes [yN]? ")
        else:
            confirmtext = (
                "Are you sure you want to commit the selected changes [yN]? ")

        response = self.confirmationwindow(confirmtext)
        if response is None:
            response = "n"
        if response.lower().startswith("y"):
            return True
        else:
            return False

    def recenterdisplayedarea(self):
        """
        once we scrolled with pg up pg down we can be pointing outside of the
        display zone. we print the patch with towin=False to compute the
        location of the selected item eventhough it is outside of the displayed
        zone and then update the scroll.
        """
        self.printitem(towin=False)
        self.updatescroll()

    def toggleamend(self, opts):
        """Toggle the amend flag.

        When the amend flag is set, a commit will modify the most recently
        committed changeset, instead of creating a new changeset.  Otherwise, a
        new changeset will be created (the normal commit behavior).

        """
        if opts.get('amend') is False:
            opts['amend'] = True
            msg = ("Amend option is turned on -- commiting the currently "
                   "selected changes will not create a new changeset, but "
                   "instead update the most recently committed changeset.\n\n"
                   "Press any key to continue.")
        elif opts.get('amend') is True:
            opts['amend'] = False
            msg = ("Amend option is turned off -- commiting the currently "
                   "selected changes will create a new changeset.\n\n"
                   "Press any key to continue.")

        self.confirmationwindow(msg)

    def emptypatch(self):
        item = self.headerlist
        if not item:
            return True
        for header in item:
            if header.hunks:
                return False
        return True

    def handlekeypressed(self, keypressed, opts):
        """
        Perform actions based on pressed keys.

        Return true to exit the main loop.
        """
        if keypressed in ["k", "KEY_UP"]:
            self.uparrowevent()
        if keypressed in ["K", "KEY_PPAGE"]:
            self.uparrowshiftevent()
        elif keypressed in ["j", "KEY_DOWN"]:
            self.downarrowevent()
        elif keypressed in ["J", "KEY_NPAGE"]:
            self.downarrowshiftevent()
        elif keypressed in ["l", "KEY_RIGHT"]:
            self.rightarrowevent()
        elif keypressed in ["h", "KEY_LEFT"]:
            self.leftarrowevent()
        elif keypressed in ["H", "KEY_SLEFT"]:
            self.leftarrowshiftevent()
        elif keypressed in ["q"]:
            raise util.Abort(_('user quit'))
        elif keypressed in ['a']:
            self.toggleamend(opts)
        elif keypressed in ["c"]:
            if self.confirmcommit():
                opts['commit'] = True
                return True
        elif keypressed in ["s"]:
            opts['commit'] = False
            if self.confirmcommit(stage=True):
                opts['commit'] = False
                return True
        elif keypressed in ["r"]:
            if self.confirmcommit(review=True):
                opts['commit'] = True
                opts['crecord_reviewpatch'] = True
                return True
        elif keypressed in [' ']:
            self.toggleapply()
        elif keypressed in ['A']:
            self.toggleall()
        elif keypressed in ["f"]:
            self.togglefolded()
        elif keypressed in ["F"]:
            self.togglefolded(foldParent=True)
        elif keypressed in ["?"]:
            self.helpwindow()
            self.stdscr.clear()
            self.stdscr.refresh()
        elif curses.unctrl(keypressed) in ["^L"]:
            # scroll the current line to the top of the screen
            self.scrolllines(self.selecteditemstartline)
        return False

    def main(self, stdscr, opts):
        """
        Method to be wrapped by curses.wrapper() for selecting chunks.

        """
        origsigwinchhandler = signal.signal(signal.SIGWINCH,
                                            self.sigwinchhandler)
        self.stdscr = stdscr
        self.yscreensize, self.xscreensize = self.stdscr.getmaxyx()

        curses.start_color()
        curses.use_default_colors()

        # available colors: black, blue, cyan, green, magenta, white, yellow
        # init_pair(color_id, foreground_color, background_color)
        self.initcolorpair(None, None, name="normal")
        self.initcolorpair(curses.COLOR_WHITE, curses.COLOR_MAGENTA,
                           name="selected")
        self.initcolorpair(curses.COLOR_RED, None, name="deletion")
        self.initcolorpair(curses.COLOR_GREEN, None, name="addition")
        self.initcolorpair(curses.COLOR_WHITE, curses.COLOR_BLUE, name="legend")
        # newwin([height, width,] begin_y, begin_x)
        self.statuswin = curses.newwin(self.numstatuslines, 0, 0, 0)
        self.statuswin.keypad(1) # interpret arrow-key, etc. ESC sequences

        # figure out how much space to allocate for the chunk-pad which is
        # used for displaying the patch

        # stupid hack to prevent getnumlinesdisplayed from failing
        self.chunkpad = curses.newpad(1, self.xscreensize)

        # add 1 so to account for last line text reaching end of line
        self.numpadlines = self.getnumlinesdisplayed(ignorefolding=True) + 1
        self.chunkpad = curses.newpad(self.numpadlines, self.xscreensize)

        # initialize selecteitemendline (initial start-line is 0)
        self.selecteditemendline = self.getnumlinesdisplayed(
            self.currentselecteditem, recursechildren=False)

        # option which enables/disables patch-review (in editor) step
        opts['crecord_reviewpatch'] = False

        if opts['author'] is not None:
            # make it accessible by self.ui.username()
            self.ui.setusername(opts['author'])

        while True:
            self.updatescreen()
            try:
                keypressed = self.statuswin.getkey()
            except curses.error:
                keypressed = "FOOBAR"
            if self.handlekeypressed(keypressed, opts):
                break
        signal.signal(signal.SIGWINCH, origsigwinchhandler)
