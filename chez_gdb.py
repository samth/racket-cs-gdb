"""gdb support for debugging Racket CS (Chez Scheme) processes on x86_64 Linux.

Load it with `source chez_gdb.py`. It adds:

  An unwinder and a frame filter, so that `bt` walks through Scheme
  frames and names them. Scheme frames appear with the name of their
  Chez code object; Racket procedures often have schemified names such as
  `temp50_0`, which racket/src/cs/schemified/*.scm in a build tree maps
  back to source.

  Commands:
    chez-layout [PATH]  show the layout in use, or load it from an equates.h
    chez-where          summary for the selected frame: code object,
                        Racket's `current-atomic`, closure, Scheme stack
    chez-stack [N]      the Scheme stack of the selected frame, newest
                        first, across stack segments and metacontinuation
                        frames (one per prompt); runs of a name collapse
    chez-name ADDR      name the code object containing ADDR
    chez-atomic         Racket's `current-atomic` for the selected thread
    chez-break NAME     set a breakpoint at the entry of every code object
                        named NAME
    chez-perf-map [PATH] write a perf symbol map (by default
                        /tmp/perf-PID.map) naming every code object;
                        needs chez_perf_map.py next to this file

  Convenience functions, for breakpoint conditions and the like:
    $chez_name(ADDR)            name of the code object containing ADDR
    $chez_atomic()              `current-atomic` of the selected thread
    $chez_caller_is(NAME [, N]) whether one of the N newest Scheme frames
                                (default 20) is named NAME

  For example:
    break gtk_clipboard_set_with_data if $chez_atomic() > 0
    chez-break sync-poll

Layout. Object layouts come from Chez's equates.h for ta6le. By default
the file is looked for at racket/src/build/cs/c/ChezScheme/boot/ta6le/
equates.h relative to the racket binary (as in a build tree); otherwise
built-in values checked against Racket CS 9.3.0.8 are used. Use
`chez-layout PATH` to name a file explicitly; the pb/equates.h in the same
tree is a different machine type and does not fit. Register assignments
(%r14 thread context, %r13 Scheme frame pointer, %r15 closure) come from
Chez's x86_64.ss for Linux and are fixed here.

Frames. Chez Scheme stacks grow toward higher addresses. The word at the
base of a frame is the return address into its caller, and the caller's
frame starts that return point's frame size below; the size is in the
word just before the return address (a byte count, or with the low bit
set, a compact header holding a word count). When a frame's base is the
start of its stack segment, the frame's caller is the top frame of the
continuation the segment links to; the chain ends at a one-shot
continuation that has been shot. Below that, Racket CS keeps one
metacontinuation frame per prompt, each holding the continuation to
resume; those continue the stack. This follows S_continuation_depth in
Chez's c/schsig.c.

Breakpoints on Racket procedures sit at a code object's entry, which runs
for calls through the procedure's closure: calls to primitives from other
code, calls across modules, and calls through variables or higher-order
functions. Calls the compiler resolves statically (within a module,
self-recursion, loops) jump past the entry, and inlined calls have no call
at all. The breakpoints stay valid for the Racket core (the rumble, thread,
io, and expander layers), whose code is loaded from boot files into the
static generation and never moves. Code compiled or loaded later can be
moved by the collector, which leaves such a breakpoint pointing at stale
memory.
"""

import os
import re

import gdb
from gdb.FrameDecorator import FrameDecorator
from gdb.unwinder import FrameId, Unwinder, register_unwinder

# gdb defines __file__ only while it sources this file, so note where the
# file is now, for finding chez_perf_map.py later.
_HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()

# ----------------------------------------------------------------------
# Layout

# Checked against racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h
# for Racket CS 9.3.0.8.
DEFAULT_LAYOUT = {
    "type_code": 0xBE,
    "code_type_disp": 0x1,
    "code_length_disp": 0x9,
    "code_name_disp": 0x19,
    "code_data_disp": 0x41,
    "code_flags_offset": 0x8,
    "code_flag_continuation": 0x2,
    "type_typed_object": 0x7,
    "type_closure": 0x5,
    "type_pair": 0x1,
    "closure_code_disp": 0x3,
    "type_string": 0x2,
    "string_type_disp": 0x1,
    "string_data_disp": 0x9,
    "string_length_offset": 0x4,
    "string_char_bytes": 0x4,
    "char_data_offset": 0x8,
    "type_char": 0x16,
    "sfalse": 0x6,
    "fixnum_offset": 0x3,
    "mask_fixnum": 0x7,
    "pair_car_disp": 0x7,
    "pair_cdr_disp": 0xF,
    "record_data_disp": 0x9,
    "tc_virtual_registers_disp": 0xA0,
    "virtual_register_count": 0x10,
    "tc_scheme_stack_disp": 0x140,
    "tc_scheme_stack_size_disp": 0x158,
    "tc_stack_link_disp": 0x150,
    "tc_sfp_disp": 0x38,
    "tc_cp_disp": 0x40,
    "continuation_code_disp": 0x3,
    "continuation_stack_disp": 0xB,
    "continuation_stack_length_disp": 0x13,
    "continuation_stack_clength_disp": 0x1B,
    "continuation_link_disp": 0x23,
    "continuation_return_address_disp": 0x2B,
    "scaled_shot_1_shot_flag": -0x8,
    "compact_header_mask": 0x1,
    "compact_frame_words_offset": 0x2,
    "compact_frame_words_mask": 0x1F,
    "ptr_bytes": 0x8,
}

# x86_64 Linux register assignments, from Chez's s/x86_64.ss
REG_TC = "r14"
REG_SFP = "r13"
REG_CP = "r15"


class Layout:
    def __init__(self, values, source):
        self.__dict__.update(values)
        self.source = source

    @staticmethod
    def from_equates(path):
        values = dict(DEFAULT_LAYOUT)
        found = set()
        pattern = re.compile(r"#define\s+(\w+)\s+(?:\(ptr\))?(-?0x[0-9A-Fa-f]+|-?\d+)\s*$")
        with open(path) as f:
            for line in f:
                m = pattern.match(line)
                if m and m.group(1) in values:
                    values[m.group(1)] = int(m.group(2), 0)
                    found.add(m.group(1))
        missing = set(DEFAULT_LAYOUT) - found
        if missing:
            raise gdb.GdbError("%s lacks %s" % (path, ", ".join(sorted(missing))))
        return Layout(values, path)


_layout = None
_layout_explicit = False  # set by `chez-layout PATH`; kept across objfiles


def _guess_equates():
    """equates.h in a build tree, found relative to the racket binary."""
    exe = gdb.current_progspace().filename
    if not exe:
        return None
    bindir = os.path.dirname(os.path.realpath(exe))
    path = os.path.join(bindir, "..", "src", "build", "cs", "c", "ChezScheme",
                        "boot", "ta6le", "equates.h")
    return path if os.path.exists(path) else None


def layout():
    global _layout
    if _layout is None:
        path = _guess_equates()
        _layout = (Layout.from_equates(path) if path
                   else Layout(DEFAULT_LAYOUT, "built-in values for Racket CS 9.3.0.8"))
    return _layout


# ----------------------------------------------------------------------
# Memory, with caches that last until the inferior runs again. They must
# survive the stop event: gdb unwinds the first frames to report a stop
# before the event fires, and keeps those frames for later commands.

_code_ranges = []   # (start, end, code object) found so far
_unwind_state = {}  # (pc, frame base) -> segment state, for the unwinder


def _clear_caches(*_):
    _code_ranges.clear()
    _unwind_state.clear()


def _new_objfile(*_):
    """A new program may be a different Racket build: look for its layout
    again, unless one was named explicitly."""
    global _layout
    _clear_caches()
    if not _layout_explicit:
        _layout = None


gdb.events.cont.connect(_clear_caches)
gdb.events.new_objfile.connect(_new_objfile)


def _inf():
    return gdb.selected_inferior()


def u64(a):
    return int.from_bytes(_inf().read_memory(a, 8).tobytes(), "little")


def s64(a):
    return int.from_bytes(_inf().read_memory(a, 8).tobytes(), "little", signed=True)


def u32(a):
    return int.from_bytes(_inf().read_memory(a, 4).tobytes(), "little")


def _read_block(lo, hi):
    """Bytes of [lo, hi), trimmed from the low end until readable."""
    while lo < hi:
        try:
            return lo, _inf().read_memory(lo, hi - lo).tobytes()
        except gdb.MemoryError:
            lo = (lo + 4096) & ~4095
    return hi, b""


def is_c_address(addr):
    """Whether addr is in an executable or shared library gdb knows."""
    return gdb.current_progspace().objfile_for_address(addr) is not None


# ----------------------------------------------------------------------
# Decoding Chez objects

def code_from_pc(pc, limit=1 << 20):
    """The tagged code object whose machine code contains pc, or None."""
    if pc < 0x1000 or is_c_address(pc):
        return None
    for lo, hi, p in _code_ranges:
        if lo <= pc < hi:
            return p
    L = layout()
    base, data = _read_block(max(pc - limit, 0x1000) & ~7, (pc & ~7) + 8)
    a = (pc & ~7) - base
    while a >= 0:
        if data[a] == L.type_code:
            # the type word is at p + code_type_disp
            p = base + a - L.code_type_disp
            off = p + L.code_length_disp - base
            if off + 8 <= len(data):
                n = int.from_bytes(data[off:off + 8], "little")
            else:
                try:
                    n = u64(p + L.code_length_disp)
                except gdb.MemoryError:
                    n = 0
            start = p + L.code_data_disp
            if 0 < n < (1 << 24) and start <= pc < start + n:
                _code_ranges.append((start, start + n, p))
                return p
        a -= 8
    return None


def scheme_string(s):
    L = layout()
    if s == L.sfalse:
        return "#f"
    if s & 7 != L.type_typed_object:
        return "<non-string %#x>" % s
    tw = u64(s + L.string_type_disp)
    if tw & 7 != L.type_string:
        return "<non-string %#x>" % s
    n = tw >> L.string_length_offset
    if n > 400:
        return "<long name>"
    data = _inf().read_memory(s + L.string_data_disp, n * L.string_char_bytes).tobytes()
    return "".join(chr(int.from_bytes(data[4 * i:4 * i + 4], "little") >> L.char_data_offset)
                   for i in range(n))


def code_name(p):
    if not p:
        return "<no code object>"
    return scheme_string(u64(p + layout().code_name_disp))


def fixnum(v):
    L = layout()
    return v >> L.fixnum_offset if v & L.mask_fixnum == 0 else None


def frame_size(ret):
    """Size in bytes of the frame that return address ret returns into."""
    L = layout()
    w = u64(ret - 8)
    if w & L.compact_header_mask:
        return ((w >> L.compact_frame_words_offset) & L.compact_frame_words_mask) * L.ptr_bytes
    return w


def code_of_entry(entry):
    """The tagged code object whose machine code starts at entry. Closures
    and continuations hold their code's entry address, not the object."""
    return entry - layout().code_data_disp


def is_continuation(k):
    L = layout()
    if k & 7 != L.type_closure:
        return False
    try:
        tw = u64(code_of_entry(u64(k + L.continuation_code_disp)) + L.code_type_disp)
    except gdb.MemoryError:
        return False
    return (tw & 0xFF) == L.type_code and ((tw >> L.code_flags_offset) & L.code_flag_continuation) != 0


def is_shot(k):
    """Whether k ends a chain: a shot one-shot continuation, like the null
    continuation."""
    L = layout()
    return s64(k + L.continuation_stack_length_disp) == L.scaled_shot_1_shot_flag


# ----------------------------------------------------------------------
# Threads

def virtual_register(tc, i):
    L = layout()
    return u64(tc + L.tc_virtual_registers_disp + 8 * i)


def current_atomic(tc):
    return fixnum(virtual_register(tc, layout().virtual_register_count - 1))


def tc_holds(tc, sfp):
    """Whether sfp lies in tc's current Scheme stack segment."""
    L = layout()
    try:
        stk = u64(tc + L.tc_scheme_stack_disp)
        size = u64(tc + L.tc_scheme_stack_size_disp)
    except gdb.MemoryError:
        return False
    return stk <= sfp < stk + size


def frame_regs(frame):
    return tuple(int(frame.read_register(r)) for r in ("pc", REG_SFP, REG_TC, REG_CP))


def newest_scheme_frame(frame):
    """frame, or the first older frame whose pc is in Scheme code, as when
    stopped in C code that Scheme called; None if there is none nearby."""
    for _ in range(64):
        if frame is None:
            return None
        try:
            if code_from_pc(frame.pc()) is not None:
                return frame
        except gdb.MemoryError:
            pass
        frame = frame.older()
    return None


def thread_context(frame=None):
    """(pc, sfp, tc, cp) for the newest Scheme frame at or below frame, by
    default the selected frame. When the registers there are not Scheme's,
    get the tc from thread-specific data and the frame pointer and closure
    Chez saved in it."""
    frame = frame or gdb.selected_frame()
    frame = newest_scheme_frame(frame) or frame
    pc, sfp, tc, cp = frame_regs(frame)
    if tc_holds(tc, sfp):
        return pc, sfp, tc, cp
    L = layout()
    try:
        # pthread_key_t is an unsigned int; gdb needs the types spelled out
        tc = int(gdb.parse_and_eval(
            "(unsigned long)((void *(*)(unsigned int))pthread_getspecific)"
            "(*(unsigned int *)&S_tc_key)"))
    except gdb.error as e:
        raise gdb.GdbError("cannot find this thread's Chez thread context: %s" % e)
    return pc, u64(tc + L.tc_sfp_disp), tc, u64(tc + L.tc_cp_disp)


def metacontinuation_ks(tc):
    """The continuations to resume for each of Racket's metacontinuation
    frames, innermost first. The list is in a virtual register; take the
    first register that holds a list of records whose second field is a
    continuation."""
    L = layout()
    for r in range(L.virtual_register_count):
        v = virtual_register(tc, r)
        ks = []
        try:
            while v & 7 == L.type_pair and len(ks) < 100000:
                e = u64(v + L.pair_car_disp)
                if e & 7 != L.type_typed_object:
                    break
                k = u64(e + L.record_data_disp + 8)
                if not is_continuation(k):
                    break
                ks.append(k)
                v = u64(v + L.pair_cdr_disp)
        except gdb.MemoryError:
            continue
        if ks:
            return ks
    return []


# ----------------------------------------------------------------------
# Walking the Scheme stack
#
# A position is (pc, base, seg): the frame at `base` is executing at `pc`,
# and seg = (segment start, next k or None for the tc's stack link,
# index of the next metacontinuation frame).

def _enter_k(tc, k, mc_index):
    """The position of the top frame of continuation k, following links and
    metacontinuation frames past shot continuations; None at the end."""
    L = layout()
    mcs = None
    while True:
        while k is not None and is_continuation(k) and not is_shot(k):
            stk = u64(k + L.continuation_stack_disp)
            clen = u64(k + L.continuation_stack_clength_disp)
            ret = u64(k + L.continuation_return_address_disp)
            if clen > 0:
                return ret, stk + clen - frame_size(ret), (stk, k, mc_index)
            k = u64(k + L.continuation_link_disp)
        if mcs is None:
            mcs = metacontinuation_ks(tc)
        if mc_index >= len(mcs):
            return None
        k = mcs[mc_index]
        mc_index += 1


def first_position(tc, pc, sfp):
    L = layout()
    return pc, sfp, (u64(tc + L.tc_scheme_stack_disp), None, 0)


def caller_position(tc, pos):
    """The position of the frame that the frame at pos returns to."""
    L = layout()
    pc, base, (seg_start, k, mc_index) = pos
    if base > seg_start:
        ret = u64(base)
        return ret, base - frame_size(ret), (seg_start, k, mc_index)
    nxt = (u64(tc + L.tc_stack_link_disp) if k is None
           else u64(k + L.continuation_link_disp))
    return _enter_k(tc, nxt, mc_index)


def walk(tc, pc, sfp, limit=100000):
    """Yield (pc, base, mc_index) for each Scheme frame, newest first."""
    pos = first_position(tc, pc, sfp)
    for _ in range(limit):
        if pos is None:
            return
        yield pos[0], pos[1], pos[2][2]
        pos = caller_position(tc, pos)


def stack_names(tc, pc, sfp, limit=100000):
    """Names of the Scheme frames, newest first, with metacontinuation
    boundaries marked and runs of a name collapsed."""
    out = []
    last_mc = 0
    for fpc, _, mc in walk(tc, pc, sfp, limit):
        if mc != last_mc:
            out.append(["---- metacontinuation frame %d ----" % (mc - 1), 1])
            last_mc = mc
        name = code_name(code_from_pc(fpc))
        if out and out[-1][0] == name:
            out[-1][1] += 1
        else:
            out.append([name, 1])
    return out


# ----------------------------------------------------------------------
# Unwinder

class ChezUnwinder(Unwinder):
    """Unwinds Scheme frames. Each Scheme frame keeps the C stack pointer
    of the code that entered Scheme, and gets %r13 and %r14 set to its own
    frame base and thread context; its frame id pairs that stack pointer
    with the code object's entry and, to tell recursive frames apart, the
    frame base. After the last Scheme frame, unwinding resumes in C at the
    first word above the stack pointer that is a return address into C."""

    def __init__(self):
        super().__init__("chez")

    def __call__(self, pending):
        try:
            return self._unwind(pending)
        except gdb.error:
            # unreadable memory or an unavailable register: not ours to unwind
            return None

    def _unwind(self, pending):
        pc = int(pending.read_register("pc"))
        code = code_from_pc(pc)
        if code is None:
            return None
        sfp = int(pending.read_register(REG_SFP))
        rsp = int(pending.read_register("rsp"))
        state = _unwind_state.get((pc, sfp))
        if state is None:
            tc = int(pending.read_register(REG_TC))
            if not tc_holds(tc, sfp):
                return None
            pos = first_position(tc, pc, sfp)
        else:
            tc, pos = state
        caller = caller_position(tc, pos)

        L = layout()
        info = pending.create_unwind_info(FrameId(rsp, code + L.code_data_disp, sfp))
        pc_type = pending.read_register("pc").type
        reg_type = pending.read_register("rsp").type
        if caller is not None:
            cpc, cbase, _ = caller
            _unwind_state[(cpc, cbase)] = (tc, caller)
            info.add_saved_register("pc", gdb.Value(cpc).cast(pc_type))
            info.add_saved_register("rsp", gdb.Value(rsp).cast(reg_type))
            info.add_saved_register(REG_SFP, gdb.Value(cbase).cast(reg_type))
            info.add_saved_register(REG_TC, gdb.Value(tc).cast(reg_type))
        else:
            ret_at = self._c_return(rsp)
            if ret_at is None:
                info.add_saved_register("pc", gdb.Value(0).cast(pc_type))
                info.add_saved_register("rsp", gdb.Value(rsp).cast(reg_type))
            else:
                info.add_saved_register("pc", gdb.Value(u64(ret_at)).cast(pc_type))
                info.add_saved_register("rsp", gdb.Value(ret_at + 8).cast(reg_type))
        return info

    @staticmethod
    def _c_return(rsp, limit=4096):
        """Where the return address into the C code that entered Scheme is:
        the first word above rsp inside a C function gdb can name. This is
        a heuristic; a stale code pointer above rsp would mislead it."""
        _, data = _read_block(rsp, rsp + limit)
        for i in range(0, len(data) - 7, 8):
            v = int.from_bytes(data[i:i + 8], "little")
            if is_c_address(v) and _c_symbol(v):
                return rsp + i
        return None


def _c_symbol(addr):
    s = gdb.execute("info symbol %#x" % addr, to_string=True).strip()
    return None if s.startswith("No symbol") else s


# ----------------------------------------------------------------------
# Frame filter: names for Scheme frames in `bt`

class ChezFrameDecorator(FrameDecorator):
    def function(self):
        frame = self.inferior_frame()
        try:
            code = code_from_pc(frame.pc())
        except gdb.MemoryError:
            code = None
        if code is not None:
            return "[scheme] %s" % code_name(code)
        return super().function()


class ChezFrameFilter:
    def __init__(self):
        self.name = "chez"
        self.priority = 100
        self.enabled = True
        gdb.current_progspace().frame_filters[self.name] = self

    def filter(self, frames):
        return map(ChezFrameDecorator, frames)


# ----------------------------------------------------------------------
# Commands

class ChezLayout(gdb.Command):
    """Show the Chez layout in use, or load it from an equates.h: chez-layout [PATH]"""

    def __init__(self):
        super().__init__("chez-layout", gdb.COMMAND_DATA, gdb.COMPLETE_FILENAME)

    def invoke(self, arg, from_tty):
        global _layout, _layout_explicit
        if arg.strip():
            _layout = Layout.from_equates(os.path.expanduser(arg.strip()))
            _layout_explicit = True
            _clear_caches()
        print("layout from %s" % layout().source)


class ChezWhere(gdb.Command):
    """Summarize the selected frame's Chez state: chez-where"""

    def __init__(self):
        super().__init__("chez-where", gdb.COMMAND_STACK)

    def invoke(self, arg, from_tty):
        here = int(gdb.selected_frame().pc())
        if code_from_pc(here) is None:
            print("pc %#x in C: %s" % (here, _c_symbol(here) or "unknown code"))
        pc, sfp, tc, cp = thread_context()
        code = code_from_pc(pc)
        if code:
            print("%s %#x in Scheme code: %s"
                  % ("pc" if pc == here else "newest Scheme frame at", pc, code_name(code)))
        print("tc %#x, sfp %#x, current-atomic %s"
              % (tc, sfp, current_atomic(tc)))
        L = layout()
        if cp & 7 == L.type_closure:
            print("closure %#x: %s" % (cp, code_name(code_of_entry(u64(cp + L.closure_code_disp)))))
        ChezStack.print_stack(tc, pc, sfp, 40)


class ChezStack(gdb.Command):
    """Print the Scheme stack of the selected frame, newest first: chez-stack [N]"""

    def __init__(self):
        super().__init__("chez-stack", gdb.COMMAND_STACK)

    @staticmethod
    def print_stack(tc, pc, sfp, n):
        names = stack_names(tc, pc, sfp)
        for name, count in names[:n]:
            print("  %s%s" % (name, " x%d" % count if count > 1 else ""))
        if len(names) > n:
            print("  ... %d more" % (len(names) - n))

    def invoke(self, arg, from_tty):
        n = int(arg) if arg.strip() else 1000
        pc, sfp, tc, _ = thread_context()
        self.print_stack(tc, pc, sfp, n)


class ChezName(gdb.Command):
    """Name the code object containing an address: chez-name ADDR"""

    def __init__(self):
        super().__init__("chez-name", gdb.COMMAND_DATA, gdb.COMPLETE_EXPRESSION)

    def invoke(self, arg, from_tty):
        v = gdb.parse_and_eval(arg)
        if v.type.code == gdb.TYPE_CODE_FUNC:
            # a function name evaluates to the function; name its address
            v = v.address
        addr = int(v)
        code = code_from_pc(addr)
        if code is None:
            print("%#x is not in Scheme code%s" % (addr, ": " + _c_symbol(addr) if _c_symbol(addr) else ""))
        else:
            start = code + layout().code_data_disp
            print("%#x is %s+%d (code object %#x)" % (addr, code_name(code), addr - start, code))


class ChezAtomic(gdb.Command):
    """Print Racket's current-atomic for the selected thread: chez-atomic"""

    def __init__(self):
        super().__init__("chez-atomic", gdb.COMMAND_DATA)

    def invoke(self, arg, from_tty):
        _, _, tc, _ = thread_context()
        print(current_atomic(tc))


def _mappings():
    """(start, end) of the inferior's anonymous memory mappings and its
    heap, where Chez allocates."""
    out = gdb.execute("info proc mappings", to_string=True)
    maps = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].startswith("0x") and "r" in parts[4]:
            name = parts[5] if len(parts) >= 6 else ""
            if name in ("", "[heap]"):
                maps.append((int(parts[0], 16), int(parts[1], 16)))
    return maps


def _search_all(needle):
    """Addresses of every occurrence of the bytes needle in the mappings."""
    inf = _inf()
    for lo, hi in _mappings():
        a = lo
        while a < hi:
            try:
                hit = inf.search_memory(a, hi - a, needle)
            except gdb.MemoryError:
                break
            if hit is None:
                break
            yield hit
            a = hit + 1


def find_code_objects(name):
    """Code objects named `name`, or whose names start with `name` without
    its trailing `*`: find the name's Chez strings, then the code objects
    whose name field points to one of them."""
    L = layout()
    prefix = name.endswith("*")
    text = name[:-1] if prefix else name
    pattern = b"".join(((ord(c) << L.char_data_offset) | L.type_char).to_bytes(4, "little")
                       for c in text)
    strings = {}
    for hit in _search_all(pattern):
        s = hit - L.string_data_disp
        try:
            found = scheme_string(s)
        except gdb.MemoryError:
            continue
        if found == text or (prefix and found.startswith(text)):
            strings[s] = found
    codes = []
    for s, found in strings.items():
        for hit in _search_all(s.to_bytes(8, "little")):
            p = hit - L.code_name_disp
            try:
                if u64(p + L.code_type_disp) & 0xFF == L.type_code:
                    codes.append((p, found))
            except gdb.MemoryError:
                pass
    return codes


class ChezBreak(gdb.Command):
    """Break at the entry of every code object with a given name: chez-break NAME

A trailing * matches names by prefix; Racket compiles procedures with
keyword or optional arguments into variants with suffixes such as `.1`,
so `chez-break sync-poll*` also catches `sync-poll.1`. Reliable for the
Racket core, whose code never moves; see the notes at the top of
chez_gdb.py."""

    def __init__(self):
        super().__init__("chez-break", gdb.COMMAND_BREAKPOINTS)

    def invoke(self, arg, from_tty):
        name = arg.strip()
        codes = find_code_objects(name)
        if not codes:
            print("no code object named %s" % name)
        for p, found in codes:
            print("%s: code object %#x" % (found, p))
            gdb.Breakpoint("*%#x" % (p + layout().code_data_disp))


class ChezPerfMap(gdb.Command):
    """Write a perf symbol map naming every Chez code object: chez-perf-map [PATH]

By default the map goes to /tmp/perf-PID.map, where `perf report` looks for
it. Add --unique to keep code objects with the same name apart."""

    def __init__(self):
        super().__init__("chez-perf-map", gdb.COMMAND_DATA, gdb.COMPLETE_FILENAME)

    def invoke(self, arg, from_tty):
        import sys
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        try:
            import chez_perf_map
        except ImportError:
            raise gdb.GdbError("chez-perf-map needs chez_perf_map.py in %s" % _HERE)
        words = arg.split()
        unique = "--unique" in words
        paths = [w for w in words if w != "--unique"]
        pid = _inf().pid
        path = os.path.expanduser(paths[0]) if paths else "/tmp/perf-%d.map" % pid

        def read(addr, n):
            try:
                return _inf().read_memory(addr, n).tobytes()
            except gdb.MemoryError:
                return None

        entries = list(chez_perf_map.find_code_objects(read, _mappings(), layout()))
        chez_perf_map.write_perf_map(path, entries, unique)
        print("wrote %d code objects to %s" % (len(entries), path))


# ----------------------------------------------------------------------
# Convenience functions

class ChezNameFn(gdb.Function):
    """$chez_name(ADDR): name of the code object containing ADDR, or ""."""

    def __init__(self):
        super().__init__("chez_name")

    def invoke(self, addr):
        code = code_from_pc(int(addr))
        return code_name(code) if code else ""


class ChezAtomicFn(gdb.Function):
    """$chez_atomic(): Racket's current-atomic for the selected thread."""

    def __init__(self):
        super().__init__("chez_atomic")

    def invoke(self):
        _, _, tc, _ = thread_context()
        return current_atomic(tc)


class ChezCallerIsFn(gdb.Function):
    """$chez_caller_is(NAME [, N]): whether one of the N newest Scheme frames
    of the selected frame (default 20) is named NAME."""

    def __init__(self):
        super().__init__("chez_caller_is")

    def invoke(self, name, n=None):
        name = name.string()
        n = int(n) if n is not None else 20
        pc, sfp, tc, _ = thread_context()
        for i, (fpc, _, _) in enumerate(walk(tc, pc, sfp)):
            if i >= n:
                break
            if code_name(code_from_pc(fpc)) == name:
                return True
        return False


register_unwinder(None, ChezUnwinder(), replace=True)
ChezFrameFilter()
for cls in (ChezLayout, ChezWhere, ChezStack, ChezName, ChezAtomic, ChezBreak,
            ChezPerfMap, ChezNameFn, ChezAtomicFn, ChezCallerIsFn):
    cls()
