# racket-cs-gdb

gdb support for debugging Racket CS processes on x86_64 Linux.

Racket CS runs on Chez Scheme, whose machine code has no symbols or unwind
information that gdb understands. By default, a backtrace from inside Racket
code shows a few `??` frames and then gives up or wanders. `chez_gdb.py`
teaches gdb to walk Chez Scheme stacks and to name the code in them:

```
(gdb) bt
#0  0x0000000041991500 in [scheme] my-loop ()
#1  0x00000000430376dc in [scheme] eval-one-top ()
#2  0x0000000042a02158 in [scheme] call-in-empty-metacontinuation-frame ()
#3  0x0000000043151b07 in [scheme] eval-all ()
#4  0x0000000043157733 in [scheme] proc ()
#5  0x0000000042a02158 in [scheme] call-in-empty-metacontinuation-frame ()
#6  0x0000000043b4e1a6 in [scheme] thunk_0 ()
#7  0x0000000042a02158 in [scheme] call-in-empty-metacontinuation-frame ()
#8  0x00000000434837af in [scheme] #f ()
#9  0x00000000433fa9c9 in [scheme] call-with-empty-metacontinuation-frame-for-swap ()
#10 0x0000600e3260da7a in S_call_help ()
#11 0x0000600e3260dd0d in Scall2 ()
#12 0x0000600e3260007b in racket_boot ()
#13 0x0000600e325fe2b8 in bytes_main ()
```

It also adds commands for inspecting Racket's state, convenience functions
for breakpoint conditions, and a way to set breakpoints on Racket
procedures by name. [EXAMPLES.md](EXAMPLES.md) has recipes for common
tasks: finding what a running or hung program is doing, sampling it,
seeing which Racket code calls a C function, and catching crashes.

## Contents

- [Requirements](#requirements)
- [Getting started](#getting-started)
- [Attaching to a running Racket](#attaching-to-a-running-racket)
- [Backtraces](#backtraces)
- [Commands](#commands)
- [Convenience functions](#convenience-functions)
- [Breaking on Racket procedures](#breaking-on-racket-procedures)
- [Breaking on C functions and seeing the Racket caller](#breaking-on-c-functions-and-seeing-the-racket-caller)
- [Catching crashes](#catching-crashes)
- [Reading the names](#reading-the-names)
- [Layout](#layout)
- [How it works](#how-it-works)
- [Limitations](#limitations)
- [Example: a hang in DrRacket's tests](#example-a-hang-in-drrackets-tests)
- [License](#license)

## Requirements

- x86_64 Linux. The register assignments are Chez's for that platform.
- Racket CS. Racket BC is a different runtime and is not supported.
- gdb 14 or later, built with Python (as Ubuntu's and Debian's are). The
  tool uses `gdb.unwinder.FrameId` and `Progspace.objfile_for_address`.
  It has been tested with gdb 15.1 and 17.1.
- Chez's layout constants for your Racket build; see [Layout](#layout).
  The built-in values match Racket CS 9.3.0.8.

## Getting started

Load the file in gdb:

```
(gdb) source /path/to/chez_gdb.py
```

To load it in every session, add that line to `~/.gdbinit`. It is harmless
for programs that are not Racket: the unwinder declines any frame whose
code it does not recognize, and the commands report what they cannot find.

You can also start Racket under gdb directly:

```
gdb -ex 'source /path/to/chez_gdb.py' \
    -ex 'handle all nostop noprint pass' \
    --args racket my-program.rkt
```

Racket CS uses signals internally, so `handle all nostop noprint pass`
keeps gdb from stopping on each one. Add back the signals you care about,
such as `handle SIGSEGV stop print`.

## Attaching to a running Racket

Many Linux systems set Yama's `ptrace_scope` to 1, which lets a process be
traced only by its ancestors. Then `gdb -p PID` fails for a racket you
started separately, even as the same user:

```
Could not attach to process.  If your uid matches the uid of the target
process, check the setting of /proc/sys/kernel/yama/ptrace_scope ...
```

Check the setting with `cat /proc/sys/kernel/yama/ptrace_scope`. When it
is 1, you have three choices:

1. Start the racket through `allow-ptrace`, which is in this repository:

   ```
   ./allow-ptrace racket my-program.rkt
   ```

   It calls `prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY)` and then execs the
   command. The setting survives `exec`, so any process of yours can attach
   later, and the program runs untraced until one does. This is the
   easiest way to debug a program that only misbehaves occasionally: run
   many copies through `allow-ptrace` and attach to the one that goes
   wrong.
2. Start the racket under gdb, as above.
3. With root, attach with `sudo gdb -p PID`, or set `ptrace_scope` to 0.

Then:

```
gdb -p PID -ex 'source /path/to/chez_gdb.py'
```

For a one-shot report from a script:

```
gdb -p PID -batch -ex 'source chez_gdb.py' -ex 'thread 1' -ex chez-where
```

## Backtraces

With the file loaded, `bt` walks through Scheme frames, and `frame N`,
`up` and `down` move among them. In `bt`, Scheme frames print as
`[scheme] NAME`, where NAME is the name of the Chez code object; C frames
print as usual. gdb applies frame filters only to `bt`, so `frame` and
`up` print a Scheme frame as `?? ()`; use `chez-name $pc` there. A
backtrace continues through:

- the frames on the current Scheme stack segment;
- older segments, which Chez keeps in linked continuation objects;
- Racket's metacontinuation frames. Racket CS keeps one per continuation
  prompt, and each holds the continuation to resume when the prompt's body
  returns. Frames such as `call-in-empty-metacontinuation-frame` mark these
  boundaries;
- and finally back into the C code that entered Scheme, such as
  `S_call_help` and `racket_boot` on the main thread or `start_thread` on
  another OS thread.

The stack is per OS thread. Racket's green threads are not OS threads: a
Racket thread that is not running has its continuation saved in its
engine, not on any stack, so its frames do not appear. Stopped in the
scheduler, for example, the main thread shows the scheduler's frames:

```
#0  0x00005e093f0733b0 in rktio_sleep ()
#1  0x000000004341e312 in [scheme] p ()
#2  0x00000000434afe38 in [scheme] #f ()
#3  0x00000000433b6dd5 in [scheme] process-sleep ()
#4  0x00000000433b9760 in [scheme] poll-and-select-thread! ()
#5  0x00000000431d29c9 in [scheme] call-with-empty-metacontinuation-frame-for-swap ()
#6  0x00005e093efa8a7a in S_call_help ()
```

Racket places run on their own OS threads, so `thread apply all bt`
covers them too. Here thread 2 is a place spinning in `spin-in-place`:

```
Thread 2 (Thread 0x7455043ff6c0 (LWP 287492) "racket"):
#0  0x00000000402fde24 in [scheme] spin-in-place ()
#1  0x0000000043138ff9 in [scheme] proc ()
#2  0x0000000042f36158 in [scheme] call-in-empty-metacontinuation-frame ()
```

## Commands

### `chez-where`

A summary of the selected frame: what code it is in, the thread context,
Racket's `current-atomic` counter, the current closure, and the first 40
names on the Scheme stack.

```
(gdb) chez-where
pc 0x41991500 in Scheme code: my-loop
tc 0x628bfac822e0, sfp 0x43d078e8, current-atomic 0
closure 0x43b37cad: my-loop
  my-loop
  eval-one-top
  call-in-empty-metacontinuation-frame
  ---- metacontinuation frame 0 ----
  eval-all
  proc
  call-in-empty-metacontinuation-frame
  ---- metacontinuation frame 1 ----
  thunk_0
  call-in-empty-metacontinuation-frame
  ---- metacontinuation frame 2 ----
  #f
  call-with-empty-metacontinuation-frame-for-swap
```

### `chez-stack [N]`

The Scheme stack of the selected frame, newest first, with metacontinuation
boundaries marked. Runs of the same name collapse into one line with a
count, such as `loop x5000`, which keeps deep recursion readable.

`N` limits the number of lines (the default is 1000). Unlike `bt`, this
command does not go through gdb's frame machinery, so it still works when
gdb's unwinding goes wrong for some other reason.

### `chez-name ADDR`

Names the code object that contains an address, with the offset into its
machine code:

```
(gdb) chez-name $pc
0x40a0551c is remainder+380 (code object 0x40a0535f)
```

For an address in C code, it says so and names the C symbol if there is
one.

### `chez-atomic`

Prints Racket's `current-atomic` counter for the selected thread: 0 when
the thread is not in atomic mode. While it is nonzero, Racket's scheduler
does not switch threads and breaks (including the one SIGINT and SIGTERM
turn into) are not delivered. A thread that is stuck with a nonzero count
is a classic cause of a Racket process that ignores everything.

### `chez-break NAME`

Sets a breakpoint at the entry of every code object named `NAME`:

```
(gdb) chez-break my-loop
my-loop: code object 0x411d54bf
Breakpoint 1 at 0x411d5500
(gdb) continue
Breakpoint 1, 0x00000000411d5500 in ?? ()
(gdb) bt 3
#0  0x00000000411d5500 in [scheme] my-loop ()
#1  0x0000000043a2f6dc in [scheme] eval-one-top ()
#2  0x00000000433fa158 in [scheme] call-in-empty-metacontinuation-frame ()
```

A trailing `*` matches by prefix, which catches the `.1`-style variants
that Racket makes of procedures with keyword or optional arguments. See
[Breaking on Racket procedures](#breaking-on-racket-procedures) for which
calls such a breakpoint sees.

### `chez-layout [PATH]`

Without an argument, says where the layout constants came from. With a
path to an `equates.h`, loads the constants from it. See [Layout](#layout).

## Convenience functions

These work anywhere gdb takes an expression, including breakpoint
conditions.

| Function | Value |
|---|---|
| `$chez_name(ADDR)` | the name of the code object containing `ADDR`, or `""` |
| `$chez_atomic()` | Racket's `current-atomic` for the selected thread |
| `$chez_caller_is(NAME [, N])` | whether one of the `N` newest Scheme frames (default 20) is named `NAME` |

Examples:

```
# stop only when the call happens in atomic mode
break gtk_clipboard_set_with_data if $chez_atomic() > 0

# stop in a C function only when a particular Racket procedure is calling it
break rktio_sleep if $chez_caller_is("process-sleep")

# at a breakpoint in Racket code, print which code object was hit
commands 2
  silent
  printf "%s\n", $chez_name($pc)
  continue
end
```

In C code, `$chez_name($pc)` is `""`. To name the Racket caller of a C
function, go `up` to the Scheme frame first, or use `bt`.

`$chez_caller_is` walks the stack each time it runs, so a condition that
uses it on a breakpoint that hits very often slows the program down.

## Breaking on Racket procedures

`chez-break` finds code objects by name: it searches memory for the name's
Chez string, then for code objects whose name field points to that
string, and sets a breakpoint at each one's first instruction.

That instruction runs for every call that goes through the procedure's
closure: calls to Racket primitives from your code, calls from another
module, and calls through a variable or a higher-order function. Calls
that the compiler resolves statically jump past it: calls within a module
to a procedure defined there, self-recursion and loops, and calls the
compiler inlined. The Racket core's layers call each other statically, so
`chez-break sync` sees your program's calls to `sync`, but
`chez-break sync-poll`, which only `sync` calls, never hits. When the
procedure you care about is called statically, break on a primitive or C
function it calls, and use `$chez_caller_is` to pick out the calls you
want.

The breakpoints also depend on the code staying put. The Racket core, meaning the
`rumble`, thread, io and expander layers that implement `sync`,
`thread-yield`, ports, `dynamic-wind`, the macro expander and so on, is
loaded from boot files into Chez's static generation, and code there never
moves. Code that Racket compiles or loads later, including your own
modules, lives in generations the collector can move. A breakpoint on such
code works until the next collection that moves it; after that it points
at stale memory. For short experiments that is usually fine: set the
breakpoint, continue, and look.

Some names find nothing:

- Racket compiles a procedure with keyword or optional arguments into
  several code objects, with names such as `sync-poll` and `sync-poll.1`.
  Use `NAME*` to catch all of them.
- Many procedures have names that schemify generated; see
  [Reading the names](#reading-the-names).

## Breaking on C functions and seeing the Racket caller

The most common use is to break in C, in rktio, the GUI's GTK calls or any
other foreign library, and ask which Racket code made the call. A plain
`break` works for this, and `bt` continues from the C frames into the
Scheme frames that called them:

```
(gdb) break rktio_sleep
(gdb) continue
Breakpoint 1, 0x00005e093f0733b0 in rktio_sleep ()
(gdb) bt 5
#0  0x00005e093f0733b0 in rktio_sleep ()
#1  0x000000004341e312 in [scheme] p ()
#2  0x00000000434afe38 in [scheme] #f ()
#3  0x00000000433b6dd5 in [scheme] process-sleep ()
#4  0x00000000433b9760 in [scheme] poll-and-select-thread! ()
```

## Catching crashes

When Racket CS code or a foreign library touches bad memory, the Chez
runtime turns the SIGSEGV into a Racket exception, "invalid memory
reference. Some debugging context lost", or into a
"nonrecoverable invalid memory reference" that kills the process. Either
way, the native state at the fault is gone. To see it, have gdb stop on
the signal first:

```
gdb -p PID -batch \
    -ex 'source chez_gdb.py' \
    -ex 'handle all nostop noprint pass' \
    -ex 'handle SIGSEGV stop print pass' \
    -ex 'handle SIGBUS stop print pass' \
    -ex continue \
    -ex 'info registers' -ex 'x/24i $pc-48' \
    -ex 'bt 40' -ex chez-where \
    -ex 'thread apply all bt 15'
```

Racket CS does not use SIGSEGV in normal operation, so any SIGSEGV is a
real fault. Because the handling passes the signal on, the program
continues as it would have after gdb records the state.

To catch a fault that happens only occasionally, start many copies
through `allow-ptrace` and attach such a gdb to each as soon as it
starts. The gdb costs almost nothing until a signal arrives.

## Reading the names

The names in `[scheme]` frames are the names of Chez code objects. For
Racket code they come in a few forms:

- **Plain names**, such as `my-loop`, `sync-poll` and `eval-one-top`: the
  name of the procedure as defined.
- **Names with a source location**, such as `[...]/private/lock.rkt:43:15`:
  an anonymous procedure that Racket named after where it was written.
  The leading `[` marks an inferred name, and the path is truncated from
  the left.
- **Generated names**, such as `temp50_0`, `temp52_0`, `proc`,
  `predicate`, `thunk_0` and `#f`. The Racket core is written in Racket,
  and schemify converts it to Chez Scheme, lifting and renaming many
  procedures along the way. A Racket CS build tree keeps the converted
  code in `racket/src/cs/schemified/*.scm` (`thread.scm`, `io.scm`,
  `expander.scm` and so on), so you can search there for a generated
  name. For example, `temp50_0` in `thread.scm` is the failure callback in
  `sync`'s polling loop, from `racket/src/thread/sync.rkt`. The same
  generated name can appear in more than one of those files, so use the
  neighboring frames to decide which one you have.
- **Chez internals**, such as `winder-dummy` (the marker frames of
  `dynamic-wind`) and `call-in-empty-metacontinuation-frame` (a Racket
  prompt boundary).

## Layout

The tool needs the offsets of fields in Chez's objects and in its thread
context. These can change from one Racket version to the next, and Chez
records them in a generated header, `equates.h`. The tool looks for it in
this order:

1. The file named with `chez-layout PATH`, if you gave one.
2. `racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h`, relative to the
   racket binary being debugged, which is where it lives in a Racket build
   tree.
3. Built-in values, which match Racket CS 9.3.0.8.

`chez-layout` with no argument says which one is in use. If you debug a
Racket installed from a distribution, there is no build tree. Use the
`equates.h` from a build of the same version, or rely on the built-in
values if the version matches.

A build tree also contains `racket/src/ChezScheme/boot/pb/equates.h`. That
file is for Chez's portable bytecode machine type, and its offsets differ;
use the `ta6le` one.

If the layout is wrong, the symptoms are names like `<non-string ...>`, a
`current-atomic` that is not a small number, or backtraces that stop
early.

## How it works

**Finding code objects.** Given an address, the tool scans backward for a
word whose low byte is Chez's code-object type tag and checks that the
object's length covers the address. It caches the ranges it finds until
the program runs again, since code can move while it runs.

**Registers.** On x86_64 Linux, Chez keeps the thread context (`tc`) in
`%r14`, the Scheme frame pointer in `%r13` and the current closure in
`%r15`. These are callee-saved in the C ABI, so gdb recovers their values
in Scheme frames even when the program stopped in C. If they do not look
like Scheme's, the tool asks the program for its thread context through
`pthread_getspecific(S_tc_key)`; that needs a live process.

**Frames.** Chez Scheme stacks grow toward higher addresses. The word at
the base of a frame is the return address into its caller. The caller's
frame starts that return point's frame size below. The size is in the
word just before the return address: a byte count, or, when the low bit is
set, a compact header with a word count. This follows
`S_continuation_depth` in Chez's `c/schsig.c`.

**Segments.** When a frame's base is the start of its stack segment, its
caller is the top frame of the continuation that the segment links to
(`tc->stack_link` for the current segment, the continuation's own link
otherwise). A chain ends at a one-shot continuation that has been shot,
such as Chez's null continuation.

**Metacontinuations.** Below that, Racket CS keeps a list of
metacontinuation frames in one of Chez's virtual registers. Each is a
record whose second field is the continuation to resume. The tool takes
the first virtual register that holds a list of such records, and
continues with each continuation in turn.

**Atomic mode.** Racket's `current-atomic` counter is the last of Chez's
virtual registers.

**The unwinder.** For each frame whose pc is in a Chez code object, the
unwinder computes the caller's pc and frame base with the rules above. The
caller keeps the C stack pointer of the code that entered Scheme, gets
`%r13` and `%r14` set to its own frame base and thread context, and gets a
frame id that pairs that stack pointer with the code object's entry and,
to tell recursive frames apart, the frame base. After the last Scheme
frame, unwinding resumes in C at the first word above the stack pointer
that is a return address into a C function gdb can name.

**The frame filter** replaces the function name of each Scheme frame with
`[scheme] NAME`.

## Limitations

- **x86_64 Linux only.** Other platforms assign Chez's registers
  differently, and the tool does not know those assignments.
- **The hand-off back to C is a heuristic.** A stale code pointer on the
  C stack above Scheme's stack pointer could make the frames after the
  Scheme ones wrong. The Scheme frames themselves are unaffected, and
  `chez-stack` does not depend on the hand-off at all.
- **Register values in Scheme frames are synthetic.** In a Scheme frame
  other than the innermost, `info registers` shows `%rsp` as the C stack
  pointer where Scheme was entered and `%r13`/`%r14` as the frame's base and
  thread context. Other registers are gdb's guesses.
- **Frames whose code the tool cannot find stop the walk.** This can
  happen in code that does not follow Chez's frame conventions, such as
  the assembly glue that enters and leaves Scheme. The walk then ends at
  that frame or falls back to gdb's own unwinders.
- **`chez-break` sees only calls through closures.** Calls that the
  compiler resolves statically, inlines or turns into loops skip the
  entry where the breakpoint is.
- **Movable code.** Breakpoints set with `chez-break` on code outside the
  static generation go stale when the collector moves the code.
- **Racket threads.** Only the stack of the Racket thread that is running
  on each OS thread is visible.
- **Core files.** The walk and the names work on a core file. The fallback
  that calls `pthread_getspecific` does not, since it runs code in the
  process.

## Example: a hang in DrRacket's tests

This tool grew out of a DrRacket test that hung occasionally under load:
one core at 100%, and SIGTERM ignored. The investigation went roughly like
this.

1. Running many copies under load through `allow-ptrace` reproduced the
   hang in about 2% of runs.
2. Samples of a hung process showed `current-atomic` at 1 or 2 every
   time, with the main thread running `sync`'s polling code and the
   runtime helpers it calls. In atomic mode, `thread-yield` cannot switch
   threads, so the thread that would have ended the wait never ran.
3. Logging added to the GUI library showed that an event callback had
   returned in atomic mode after an "invalid memory reference" exception.
4. A gdb attached to every run with `handle SIGSEGV stop` caught the fault
   in `g_str_hash`, under `gtk_clipboard_set_with_data`, reading a string
   pointer of `0x6`, which is Chez's `#f`. The array of clipboard targets
   had been allocated in memory the collector can move, and GTK calls
   back into Racket (through the previous clipboard owner's clear
   callback) before it reads the array. A collection during that callback
   moved the array.

The fix allocates the array with `'raw` memory, which does not move.

## License

racket-cs-gdb is distributed under the MIT license and the Apache License,
version 2.0, at your option, like Racket. See [LICENSE.txt](LICENSE.txt).
