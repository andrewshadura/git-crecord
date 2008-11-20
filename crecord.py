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

class header(object):
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

class hunk(object):
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
        self.added, self.removed = self.countchanges(self.hunk)
        
        # flag to indicate whether to apply this chunk
        self.applied = True
    
    @staticmethod
    def countchanges(hunk):
        """hunk -> (n+,n-)"""
        add = len([h for h in hunk if h[0] == '+'])
        rem = len([h for h in hunk if h[0] == '-'])
        return add, rem

    def write(self, fp):
        delta = len(self.before) + len(self.after)
        if self.after and self.after[-1] == '\\ No newline at end of file\n':
            delta -= 1
        fromlen = delta + self.removed
        tolen = delta + self.added
        fp.write('@@ -%d,%d +%d,%d @@%s\n' %
                 (self.fromline, fromlen, self.toline, tolen,
                  self.proc and (' ' + self.proc)))
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
    
    # let user choose headers/hunks, and mark their applied flags accordingly
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
    chunkSelector = CursesChunkSelector(headerList)
    curses.wrapper(chunkSelector.main)
    
    # todo: move this to a curses confirmation dialog
    query = 'are you sure you want to commit the selected chunks [yN]? '
    r = raw_input(query).lower()
    if not r.startswith('y'):
        raise util.Abort(_('user quit'))
    

class CursesChunkSelector(object):
    def __init__(self, headerList):
        self.headerList = headerList
        
        # list of all chunks
        self.chunkList = []
        for h in headerList:
            self.chunkList.append(h)
            self.chunkList.extend(h.hunks)
        
        self.firstChunkToDisplay = 0
        
        # really, a 'last chunk that was displayed' variable
        self.lastChunkToDisplay = None # updated when printing chunks to current display 
        self.selectedChunkIndex = 0
        self.lastKeyPressed = ""
        
    
    def scroll(self, numHunks):
        """
        numHunks > 0 causes the screen to scroll up (like pressing page-down key).  numHunks < 0
        does the reverse.
        
        """
        self.firstChunkToDisplay += numHunks
        self.firstChunkToDisplay = min(self.firstChunkToDisplay, len(self.chunkList)-1) 
        self.firstChunkToDisplay = max(self.firstChunkToDisplay, 0)
    
    def upArrowEvent(self):
        """
        If the cursor is already at the top chunk, scroll the screen down and move the cursor-position
        to the previous visible chunk.  Otherwise, only move the cursor position up one visible chunk.
        
        Also deal with folded headers.
        
        """
        # calculate number of chunks to move/scroll by
        if self.selectedChunkIndex != 0:
            preceedingChunk = self.chunkList[self.selectedChunkIndex-1]
            if isinstance(preceedingChunk, hunk) and preceedingChunk.header.folded:
                chunksToSkip = len(preceedingChunk.header.hunks) + 1
            else:
                chunksToSkip = 1
        if (self.selectedChunkIndex == self.firstChunkToDisplay) and self.selectedChunkIndex > 0:
            self.selectedChunkIndex -= chunksToSkip
            self.scroll(-chunksToSkip)
        elif (self.selectedChunkIndex > self.firstChunkToDisplay):
            self.selectedChunkIndex -= chunksToSkip

    def downArrowEvent(self):
        """
        If the cursor is already at the bottom chunk, scroll the screen up and move the cursor-position
        to the subsequent chunk.  Otherwise, only move the cursor position down one chunk.
        
        """
        # calculate number of chunks to move/scroll by
        if self.selectedChunkIndex != len(self.chunkList)-1:
            currentChunk = self.chunkList[self.selectedChunkIndex]
            if isinstance(currentChunk, header) and currentChunk.folded:
                chunksToSkip = len(currentChunk.hunks) + 1
            else:
                chunksToSkip = 1
        
        if (self.selectedChunkIndex >= self.lastChunkToDisplay) and self.selectedChunkIndex < len(self.chunkList)-1:
            self.selectedChunkIndex += chunksToSkip
            self.scroll(chunksToSkip)
        elif (self.selectedChunkIndex < self.lastChunkToDisplay):
            self.selectedChunkIndex += chunksToSkip
    
    def toggleApply(self, chunkIndex):
        "Toggle the applied flag of the specified chunk with index chunkIndex"
        chunk = self.chunkList[chunkIndex]
        chunk.applied = not chunk.applied
        
        if isinstance(chunk, header):
            if chunk.applied and not chunk.special():
                # apply all its hunks
                for hnk in chunk.hunks:
                    hnk.applied = True
            else:
                # un-apply all its hunks
                for hnk in chunk.hunks:
                    hnk.applied = False
        else: # chunk is a hunk
            # if all 'sibling' hunks are not-applied
            if not (True in [hnk.applied for hnk in chunk.header.hunks]) and \
                                                    not chunk.header.special():
                chunk.header.applied = False
            # apply the header if its not applied and we're applying a child hunk
            if chunk.applied and not chunk.header.applied:
                chunk.header.applied = True
    
    def toggleFolded(self, chunkIndex):
        "Toggle the folded flag of the specified chunk with index chunkIndex"
        chunk = self.chunkList[chunkIndex]
        
        if isinstance(chunk, header):
            chunk.folded = not chunk.folded
        
    def updateScreen(self):
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_WHITE) # applied
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK) # unapplied
        curses.init_pair(3, curses.COLOR_BLUE, curses.COLOR_RED) # selected
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE) # status/legend
        #self.stdscr.clear()
        #self.stdscr.refresh()
        
        self.statuswin.erase()
        #self.statuswin.refresh()
        try:
            self.statuswin.addstr("SELECT CHUNKS: (j/k/up/down) move cursor; (space) toggle applied; (q)uit", curses.color_pair(4))
            self.statuswin.addstr("\n (f)old/unfold header; (c)ommit applied  |  [X]=hunk applied **=folded", curses.color_pair(4))
        except curses.error:
            pass
        #self.statuswin.refresh()      
        
        self.chunkwin.erase()
        chunkStringList = []
        for c in self.chunkList:
            chunkStringList.append((c.applied, c.prettyStr()))
        
        def printText(checkBox, text, colorPair):
            textList = text.split("\n")
            self.chunkwin.addstr(checkBox + textList[0] + "\n", colorPair)
            if len(textList) > 1:
                for line in textList[1:]:
                    self.chunkwin.addstr("     " + line + "\n", colorPair)
        
        skippedChunks = 0
        for i,cs in enumerate(chunkStringList):
            #self.chunkwin.addstr("*"*30)
            applied = cs[0]
            chunkText = cs[1]
            try:
                folded = self.chunkList[i].folded
            except AttributeError: # not a header
                folded = False
            
            if applied:
                checkBox = "[X]"
            else:
                checkBox = "[ ]"
            
            if folded:
                checkBox += "**"
            else:
                checkBox += "  "
            
            #text = str(curses.COLS)
            try:
                if i >= self.firstChunkToDisplay:
                    if (isinstance(self.chunkList[i], hunk) and not self.chunkList[i].header.folded) \
                            or isinstance(self.chunkList[i], header):
                        if self.selectedChunkIndex == i:
                            printText(checkBox, chunkText, curses.color_pair(3))
                        elif applied:
                            printText(checkBox, chunkText, curses.color_pair(1))
                        else:
                            printText(checkBox, chunkText, curses.color_pair(2))
                        self.lastChunkToDisplay = i
                        skippedChunks = 0 # keeps track of chunks not displayed
                    else:
                        skippedChunks += 1
            except curses.error:
                self.lastChunkToDisplay = i - (skippedChunks + 1)
                break
        #self.chunkwin.addstr("*"*30)
        
        self.chunkwin.refresh()
        self.statuswin.refresh()
        
    def sigwinchHandler(self, n, frame):
        "Handle window resizing"
        try:
            curses.endwin()
            self.stdscr = curses.initscr()
            yScreenSize, xScreenSize = self.stdscr.getmaxyx()

            self.statuswin = curses.newwin(2,xScreenSize,0,0)
            self.chunkwin = curses.newwin(yScreenSize-2,xScreenSize,2, 0)
            #curses.resizeterm(...)
        except curses.error:
            pass
    
    def main(self, stdscr):
        """
        Method to be wrapped by curses.wrapper() for selecting chunks.
        
        """
        signal.signal(signal.SIGWINCH, self.sigwinchHandler)
        self.stdscr = stdscr
        # newwin([height, width,] begin_y, begin_x)
        self.statuswin = curses.newwin(2,0,0,0)
        self.chunkwin = curses.newwin(0,0,2, 0)

        while True:
            self.updateScreen()
            self.lastKeyPressed = keyPressed = stdscr.getch()
            if keyPressed in [ord("k"), 259]: # 259==up arrow
                self.upArrowEvent()
                #self.scroll(-1)
            elif keyPressed in [ord("j"), 258]: # 258==down arrow
                self.downArrowEvent()
                #self.scroll(1)
            elif keyPressed in [ord("q")]:
                raise util.Abort(_('user quit'))
            elif keyPressed in [ord("c")]:
                break
            elif keyPressed in [32]: # 32 == space
                self.toggleApply(self.selectedChunkIndex)
            elif keyPressed in [ord("f")]: 
                self.toggleFolded(self.selectedChunkIndex)

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

