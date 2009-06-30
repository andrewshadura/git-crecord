from mercurial.i18n import gettext, _
from mercurial import cmdutil, commands, extensions, hg, mdiff
from mercurial import util

# accomodate older versions where encoding module doesn't yet exist
from mercurial import demandimport
demandimport.ignore.append('mercurial.encoding')
try:
    import mercurial.encoding as encoding
except ImportError:
    encoding = util


import copy, cStringIO, errno, operator, os, re, tempfile
import sys, fcntl, struct, termios


import signal
import locale
from crpatch import Patch, header, hunk, HunkLine

# os.name is one of: 'posix', 'nt', 'dos', 'os2', 'mac', or 'ce'
if os.name == 'posix':
    import curses
else:
    # I have no idea if wcurses works with crecord...
    import wcurses as curses

#from curses import textpad 
import textpad # customized textpad

try:
    curses
except NameError:
    raise util.Abort(_('the python curses/wcurses module is not available/installed'))
    

# deal with unicode correctly
locale.setlocale(locale.LC_ALL, '')
code = locale.getpreferredencoding()

orig_stdout = sys.__stdout__ # used by gethw()

def gethw():
    """
    Magically get the current height and width of the window (without initscr)

    This is a rip-off of a rip-off - taken from the bpython code.  It is
    useful / necessary because otherwise curses.initscr() must be called, 
    which can leave the terminal in a nasty state after exiting.

    """
    h, w = struct.unpack(
        "hhhh", fcntl.ioctl(orig_stdout, termios.TIOCGWINSZ, "\000"*8))[0:2]
    return h, w


def chunkselector(opts, headerList):
    """
    Curses interface to get selection of chunks, and mark the applied flags
    of the chosen chunks.

    """

    chunkSelector = CursesChunkSelector(headerList)
    curses.wrapper(chunkSelector.main, opts)

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

        # stores optional text for a commit comment provided by the user
        self.commentText = ""

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
            self.toggleFolded(currentItem)

        if nextItem is None:
            # if no next item on parent-level, then no change...
            nextItem = currentItem

        self.currentSelectedItem = nextItem

    def leftArrowEvent(self):
        """
        If the current item can be folded (i.e. it is an unfolded header or
        hunk), then fold it.  Otherwise try select (if possible) the parent
        of this item.

        """
        currentItem = self.currentSelectedItem
        
        # try to fold the item
        if not isinstance(currentItem, HunkLine):
            if not currentItem.folded:
                self.toggleFolded(item=currentItem)
                return
        
        # if it can't be folded, try to select the parent item
        nextItem = currentItem.parentItem()
        
        if nextItem is None:
            # if no item on parent-level, then no change...
            nextItem = currentItem
            if not nextItem.folded:
                self.toggleFolded(item=nextItem)

        self.currentSelectedItem = nextItem

    def updateScroll(self):
        "Scroll the screen in such a way to fully show the currently-selected item."
        selStart = self.selectedItemStartLine
        selEnd = self.selectedItemEndLine
        #selNumLines = selEnd - selStart
        padStart = self.firstLineOfPadToPrint
        padEnd = padStart + self.yScreenSize - self.numStatusLines - 1
        screenMiddleLine = self.yScreenSize / 2
        # 'buffered' pad start/end values which scroll with a certain
        # top/bottom context margin
        padStartBuffered = padStart + 3
        padEndBuffered = padEnd - 3

        if selEnd > padEndBuffered:
            self.scrollLines(selEnd - padEndBuffered)
        elif selStart < padStartBuffered:
            # negative values scroll in pgup direction
            self.scrollLines(selStart - padStartBuffered)


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

        item.applied = not item.applied

        if isinstance(item, header):
            item.partial = False
            if item.applied:
                if not item.special():
                    # apply all its hunks
                    for hnk in item.hunks:
                        hnk.applied = True
                        # apply all their HunkLines
                        for hunkLine in hnk.changedLines:
                            hunkLine.applied = True
                else:
                    # all children are off (but the header is on)
                    if len(item.allChildren()) > 0:
                        item.partial = True
            else:
                # un-apply all its hunks
                for hnk in item.hunks:
                    hnk.applied = False
                    # un-apply all their HunkLines
                    for hunkLine in hnk.changedLines:
                        hunkLine.applied = False
        elif isinstance(item, hunk):
            item.partial = False
            # apply all it's HunkLines
            for hunkLine in item.changedLines:
                hunkLine.applied = item.applied

            siblingAppliedStatus = [hnk.applied for hnk in item.header.hunks]
            allSiblingsApplied = not (False in siblingAppliedStatus) 
            noSiblingsApplied = not (True in siblingAppliedStatus)

            siblingsPartialStatus = [hnk.partial for hnk in item.header.hunks]
            someSiblingsPartial = (True in siblingsPartialStatus)

            #cases where applied or partial should be removed from header

            # if no 'sibling' hunks are applied (including this hunk)
            if noSiblingsApplied:
                if not item.header.special():
                    item.header.applied = False
                    item.header.partial = False
            else: # some/all parent siblings are applied
                item.header.applied = True
                item.header.partial = (someSiblingsPartial or \
                                        not allSiblingsApplied)

        elif isinstance(item, HunkLine):
            siblingAppliedStatus = [hnkln.applied for hnkln in item.hunk.changedLines]
            allSiblingsApplied = not (False in siblingAppliedStatus) 
            noSiblingsApplied = not (True in siblingAppliedStatus)

            # if no 'sibling' lines are applied
            if noSiblingsApplied:
                item.hunk.applied = False
                item.hunk.partial = False
            elif allSiblingsApplied:
                item.hunk.applied = True
                item.hunk.partial = False
            else: # some siblings applied
                item.hunk.applied = True
                item.hunk.partial = True

            parentSiblingsAppliedStatus = [hnk.applied for hnk in item.hunk.header.hunks]
            noParentSiblingsApplied = not (True in parentSiblingsAppliedStatus)
            allParentSiblingsApplied = not (False in parentSiblingsAppliedStatus)

            parentSiblingsPartialStatus = [hnk.partial for hnk in item.hunk.header.hunks]
            someParentSiblingsPartial = (True in parentSiblingsPartialStatus)

            # if all parent hunks are not applied, un-apply header
            if noParentSiblingsApplied:
                if not item.hunk.header.special():
                    item.hunk.header.applied = False
                    item.hunk.header.partial = False
            # set the applied and partial status of the header if needed
            else: # some/all parent siblings are applied
                item.hunk.header.applied = True
                item.hunk.header.partial = (someParentSiblingsPartial or \
                                            not allParentSiblingsApplied)
    def toggleFolded(self, item=None, foldParent=False):
        "Toggle folded flag of specified item (defaults to currently selected)"
        if item is None:
            item = self.currentSelectedItem
        if foldParent or (isinstance(item, header) and item.neverUnfolded):
            if not isinstance(item, header):
                # we need to select the parent item in this case
                self.currentSelectedItem = item = item.parentItem()
            elif item.neverUnfolded:
                item.neverUnfolded = False
            
            # also fold any foldable children of the parent/current item
            if isinstance(item, header): # the original OR 'new' item
                for child in item.allChildren():
                    child.folded = not item.folded

        if isinstance(item, (header, hunk)):
            item.folded = not item.folded


    def alignString(self, inStr, window):
        """
        Add whitespace to the end of a string in order to make it fill
        the screen in the x direction.  The current cursor position is
        taken into account when making this calculation.  The string can span
        multiple lines.

        """
        y,xStart = window.getyx()
        width = self.xScreenSize
        # turn tabs into spaces
        inStr = inStr.expandtabs(4)
        strLen = len(unicode(encoding.fromlocal(inStr), code))
        numSpaces = (width - ((strLen + xStart) % width) - 1)
        return inStr + " " * numSpaces + "\n"

    def printString(self, window, text, fgColor=None, bgColor=None, pair=None,
        pairName=None, attrList=None, toWin=True, align=True, showWhtSpc=False):
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
        
        If align == True, whitespace is added to the printed string such that
        the string stretches to the right border of the window.
        
        If showWhtSpc == True, trailing whitespace of a string is highlighted.

        """
        # preprocess the text, converting tabs to spaces
        text = text.expandtabs(4)
        # Strip \n, and convert control characters to ^[char] representation
        text = re.sub(r'[\x00-\x08\x0a-\x1f]',
                lambda m:'^'+chr(ord(m.group())+64), text.strip('\n'))

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
        t = "" # variable for counting lines printed
        # if requested, show trailing whitespace
        if showWhtSpc:
            origLen = len(text)
            text = text.rstrip(' \n') # tabs have already been expanded
            strippedLen = len(text)
            numTrailingSpaces = origLen - strippedLen
        
        if toWin:
            window.addstr(text, colorPair)
        t += text

        if showWhtSpc:
                wsColorPair = colorPair | curses.A_REVERSE
                if toWin:
                    for i in range(numTrailingSpaces):
                        window.addch(curses.ACS_CKBOARD, wsColorPair)
                t += " " * numTrailingSpaces
        
        if align:
            if toWin:
                extraWhiteSpace = self.alignString("", window)
                window.addstr(extraWhiteSpace, colorPair)
            else:
                # need to use t, since the x position hasn't incremented
                extraWhiteSpace = self.alignString(t, window)
            t += extraWhiteSpace
        
        # is reset to 0 at the beginning of printItem()
        
        linesPrinted = (xStart + len(t)) / self.xScreenSize
        self.linesPrintedToPadSoFar += linesPrinted
        return t

    def updateScreen(self):
        self.statuswin.erase()
        self.chunkpad.erase()

        width = self.xScreenSize
        alignString = self.alignString
        printString = self.printString

        # print out the status lines at the top
        try:
            printString(self.statuswin, "SELECT CHUNKS: (j/k/up/down/pgup/pgdn) move cursor; (space) toggle applied", pairName="legend")
            printString(self.statuswin, " (f)old/unfold; (c)ommit applied; (q)uit; (?) help | [X]=hunk applied **=folded", pairName="legend")
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
            if not isinstance(item, HunkLine) and item.partial:
                checkBox = "[~]"
            else:
                checkBox = "[X]"
        else:
            checkBox = "[ ]"

        try:
            if item.folded:
                checkBox += "**"
                if isinstance(item, header):
                    # one of "M", "A", or "D" (modified, added, deleted)
                    fileStatus = item.changetype
                 
                    checkBox += fileStatus + " "
            else:
                checkBox += "  "
                if isinstance(item, header):
                    # add two more spaces for headers
                    checkBox += "  "
        except AttributeError: # not foldable
            checkBox += "  "

        return checkBox

    def printHeader(self, header, selected=False, toWin=True, ignoreFolding=False):
        """
        Print the header to the pad.  If countLines is True, don't print
        anything, but just count the number of lines which would be printed.

        """
        outStr = ""
        text = header.prettyStr()
        chunkIndex = self.chunkList.index(header)

        if chunkIndex != 0 and not header.folded:
            # add separating line before headers
            outStr += self.printString(self.chunkpad, '_'*self.xScreenSize, toWin=toWin, align=False)
        # select color-pair based on if the header is selected
        if selected:
            colorPair = self.getColorPair(name="selected", attrList=[curses.A_BOLD])
        else:
            colorPair = self.getColorPair(name="normal", attrList=[curses.A_BOLD])

        # print out each line of the chunk, expanding it to screen width

        # number of characters to indent lines on this level by
        indentNumChars = 0
        checkBox = self.getStatusPrefixString(header)
        if not header.folded or ignoreFolding:
            textList = text.split("\n")
            lineStr = checkBox + textList[0]
        else:
            lineStr = checkBox + header.filename()
        outStr += self.printString(self.chunkpad, lineStr, pair=colorPair, toWin=toWin)
        if not header.folded or ignoreFolding:
            if len(textList) > 1:
                for line in textList[1:]:
                    lineStr = " "*(indentNumChars + len(checkBox)) + line
                    outStr += self.printString(self.chunkpad, lineStr, pair=colorPair, toWin=toWin)

        return outStr

    def printHunkLinesBefore(self, hunk, selected=False, toWin=True, ignoreFolding=False):
        "includes start/end line indicator"
        outStr = ""
        # where hunk is in list of siblings
        hunkIndex = hunk.header.hunks.index(hunk)

        if hunkIndex != 0:
            # add separating line before headers
            outStr += self.printString(self.chunkpad, ' '*self.xScreenSize, toWin=toWin, align=False)

        if selected:
            colorPair = self.getColorPair(name="selected", attrList=[curses.A_BOLD])
        else:
            colorPair = self.getColorPair(name="normal", attrList=[curses.A_BOLD])

        # print out from-to line with checkbox
        checkBox = self.getStatusPrefixString(hunk)

        linePrefix = " "*self.hunkIndentNumChars + checkBox
        frToLine = "   " + hunk.getFromToLine().strip("\n")


        outStr += self.printString(self.chunkpad, linePrefix, toWin=toWin, align=False) # add uncolored checkbox/indent
        outStr += self.printString(self.chunkpad, frToLine, pair=colorPair, toWin=toWin)

        if hunk.folded and not ignoreFolding:
            # skip remainder of output
            return outStr

        # print out lines of the chunk preceeding changed-lines
        for line in hunk.before:
            lineStr = " "*(self.hunkLineIndentNumChars + len(checkBox)) + line
            outStr += self.printString(self.chunkpad, lineStr, toWin=toWin)

        return outStr

    def printHunkLinesAfter(self, hunk, toWin=True, ignoreFolding=False):
        outStr = ""
        if hunk.folded and not ignoreFolding:
            return outStr

        indentNumChars = self.hunkLineIndentNumChars-1
        # a bit superfluous, but to avoid hard-coding indent amount
        checkBox = self.getStatusPrefixString(hunk)
        for line in hunk.after:
            lineStr = " "*(indentNumChars + len(checkBox)) + line
            outStr += self.printString(self.chunkpad, lineStr, toWin=toWin)

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
        elif lineStr.startswith("\\"):
            colorPair = self.getColorPair(name="normal")

        linePrefix = " "*indentNumChars + checkBox
        outStr += self.printString(self.chunkpad, linePrefix, toWin=toWin, align=False) # add uncolored checkbox/indent
        outStr += self.printString(self.chunkpad, lineStr, pair=colorPair, toWin=toWin, showWhtSpc=True)
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
        # TODO: eliminate all isinstance() calls
        if isinstance(item, header):
            outStr += self.printHeader(item, selected, toWin=toWin, ignoreFolding=ignoreFolding)
            if recurseChildren:
                for hnk in item.hunks:
                    self.__printItem(hnk, ignoreFolding, recurseChildren, toWin)
        elif isinstance(item, hunk) and \
        ((not item.header.folded) or ignoreFolding):
            # print the hunk data which comes before the changed-lines
            outStr += self.printHunkLinesBefore(item, selected, toWin=toWin, ignoreFolding=ignoreFolding)
            if recurseChildren:
                for line in item.changedLines:
                    self.__printItem(line, ignoreFolding, recurseChildren, toWin)
                outStr += self.printHunkLinesAfter(item, toWin=toWin, ignoreFolding=ignoreFolding)
        elif isinstance(item, HunkLine) and ((not item.hunk.folded) or ignoreFolding):
            outStr += self.printHunkChangedLine(item, selected, toWin=toWin)

        return outStr

    def getNumLinesDisplayed(self, item=None, ignoreFolding=False, recurseChildren=True):
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
            self.yScreenSize, self.xScreenSize = gethw()
            self.statuswin.resize(self.numStatusLines,self.xScreenSize)
            self.numPadLines = self.getNumLinesDisplayed(ignoreFolding=True) + 1
            self.chunkpad = curses.newpad(self.numPadLines, self.xScreenSize)
            # TODO: try to resize commit message window if possible
        except curses.error:
            pass

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

                [SPACE] : (un-)select item ([~]/[X] = partly/fully applied)
    Up/Down-arrow [k/j] : go to previous/next unfolded item
        PgUp/PgDn [K/J] : go to previous/next item of same type
 Right/Left-arrow [l/h] : go to child item / parent item
                      f : fold / unfold item, hiding/revealing its children
                      F : fold / unfold parent item and all of its ancestors
                      m : edit / resume editing the commit message
                      c : commit selected changes
                      q : quit without committing (no changes will be made)
                      ? : help (what you're currently reading)"""

        helpwin = curses.newwin(self.yScreenSize,0,0,0)
        helpLines = helpText.split("\n")
        helpLines = helpLines + [" "]*(self.yScreenSize-self.numStatusLines-len(helpLines)-1)
        try:
            for line in helpLines:
                self.printString(helpwin, line, pairName="legend")
        except curses.error:
            pass
        helpwin.refresh()
        try:
            helpwin.getkey()
        except curses.error:
            pass

    def commitMessageWindow(self):
        "Create a temporary commit message editing window on the screen."
        # In Python versions < 2.6, there is no insert mode (only overwrite) :(
        def keyFilter(key):
            "provide keymappings to emacs-style keys"
            if key in (7,):
                # diable keys we're re-mapping
                return ""
            elif key in (24, 3): # CTRL-X,  3 = CTRL-C
                return 7 # CTRL-G (i.e. exit comment window)
            else:
                return key
        statusline = curses.newwin(2,0,0,0)
        statusLineText = \
        " Begin/resume editing commit message. CTRL-C/-X returns to patch view."
        self.printString(statusline, statusLineText, pairName="legend")
        statusline.refresh()
        helpwin = curses.newwin(self.yScreenSize-1,0,1,0)
        t = textpad.Textbox(helpwin)
        curses.raw()
        self.commentText = t.edit(keyFilter, self.commentText).rstrip(" \n")
        curses.cbreak()

    def confirmCommit(self):
        "Ask for 'Y' to be pressed to confirm commit. Return True if confirmed."
        confirmText = "Are you sure you want to commit the selected changes [yN]? "

        confirmWin = curses.newwin(self.yScreenSize,0,0,0)
        try:
            self.printString(confirmWin, confirmText, pairName="selected")
        except curses.error:
            pass
        self.stdscr.refresh()
        confirmWin.refresh()
        try:
            response = chr(self.stdscr.getch())
        except ValueError:
            response = "n"
        if response.lower().startswith("y"):
            return True
        else:
            return False

    def main(self, stdscr, opts):
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
        self.statuswin.keypad(1) # interpret arrow-key, etc. ESC sequences

        # figure out how much space to allocate for the chunk-pad which is
        # used for displaying the patch

        # stupid hack to prevent getNumLinesDisplayed from failing
        self.chunkpad = curses.newpad(1,self.xScreenSize)

        # add 1 so to account for last line text reaching end of line
        self.numPadLines = self.getNumLinesDisplayed(ignoreFolding=True) + 1
        self.chunkpad = curses.newpad(self.numPadLines, self.xScreenSize)

        # initialize selecteItemEndLine (initial start-line is 0)
        self.selectedItemEndLine = self.getNumLinesDisplayed(self.currentSelectedItem, recurseChildren=False)

        try:
            self.commentText = opts['message']
        except KeyError:
            pass

        while True:
            self.updateScreen()
            try:
                keyPressed = self.statuswin.getkey()
            except curses.error:
                keyPressed = "FOOBAR"

            if keyPressed in ["k", "KEY_UP"]:
                self.upArrowEvent()
            if keyPressed in ["K", "KEY_PPAGE"]:
                self.upArrowShiftEvent()
            elif keyPressed in ["j", "KEY_DOWN"]:
                self.downArrowEvent()
            elif keyPressed in ["J", "KEY_NPAGE"]:
                self.downArrowShiftEvent()
            elif keyPressed in ["l", "KEY_RIGHT"]:
                self.rightArrowEvent()
            elif keyPressed in ["h", "KEY_LEFT"]:
                self.leftArrowEvent()
            elif keyPressed in ["q"]:
                raise util.Abort(_('user quit'))
            elif keyPressed in ["c"]:
                if self.confirmCommit():
                    break
            elif keyPressed in [' ']:
                self.toggleApply()
            elif keyPressed in ["f"]:
                self.toggleFolded()
            elif keyPressed in ["F"]:
                self.toggleFolded(foldParent=True)
            elif keyPressed in ["?"]:
                self.helpWindow()
            elif keyPressed in ["m"]:
                self.commitMessageWindow()

        if self.commentText != "":
            opts['message'] = self.commentText
