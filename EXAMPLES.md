# Examples

Recipes for common debugging tasks with `chez_gdb.py`. The output shown
comes from real runs with Racket CS 9.3.0.8 on x86_64 Linux; addresses
will differ on your machine.

In these examples, `chez_gdb.py` and `allow-ptrace` are in the current
directory, and `racket` is the Racket CS binary you are debugging. When
gdb attaches with `-p`, the program must allow it; see
[Attaching to a running Racket](README.md#attaching-to-a-running-racket).
The simplest way is to start the program through `allow-ptrace`:

```
./allow-ptrace racket work.rkt &
```

Racket CS uses signals internally. Most examples start with
`handle all nostop noprint pass` so that gdb does not stop on each one.

Several examples use this program, `work.rkt`, which alternates between
computing and sleeping:

```racket
#lang racket/base
(define (fib n) (if (< n 2) n (+ (fib (- n 1)) (fib (- n 2)))))
(define (count-primes n)
  (for/sum ([i (in-range 2 n)])
    (if (for/and ([d (in-range 2 (add1 (integer-sqrt i)))]) (positive? (remainder i d))) 1 0)))
(define (my-loop)
  (fib 32)
  (count-primes 200000)
  (sync (alarm-evt (+ (current-inexact-milliseconds) 20)))
  (my-loop))
(my-loop)
```

## Contents

- [Where is a running program?](#where-is-a-running-program)
- [A one-shot report from a script](#a-one-shot-report-from-a-script)
- [Is a hung program stuck in atomic mode?](#is-a-hung-program-stuck-in-atomic-mode)
- [A poor man's profiler](#a-poor-mans-profiler)
- [Which Racket code calls a C function?](#which-racket-code-calls-a-c-function)
- [Break in C only when a given Racket procedure is the caller](#break-in-c-only-when-a-given-racket-procedure-is-the-caller)
- [Break in C only in atomic mode](#break-in-c-only-in-atomic-mode)
- [Count calls and log the first stack](#count-calls-and-log-the-first-stack)
- [Break on a Racket procedure](#break-on-a-racket-procedure)
- [Procedures with keyword or optional arguments](#procedures-with-keyword-or-optional-arguments)
- [Log every call to a Racket procedure without stopping](#log-every-call-to-a-racket-procedure-without-stopping)
- [Catch a crash](#catch-a-crash)
- [Catch a crash that happens once in many runs](#catch-a-crash-that-happens-once-in-many-runs)
- [Look at every thread, including places](#look-at-every-thread-including-places)
- [Post-mortem from a core file](#post-mortem-from-a-core-file)
- [Name an address](#name-an-address)
- [Map a generated name back to source](#map-a-generated-name-back-to-source)
- [Debug a different Racket build](#debug-a-different-racket-build)

## Where is a running program?

Attach, and look at the main thread's stack. Here the program is a loop
`spin` called from `outer`:

```
$ gdb -p $(pgrep -n racket) -ex 'source chez_gdb.py'
(gdb) bt
#0  0x0000000040a0551c in [scheme] remainder ()
#1  0x00000000409e5afe in [scheme] modulo ()
#2  0x0000000048684813 in [scheme] spin ()
#3  0x00000000486846a1 in [scheme] outer ()
#4  0x00000000434376dc in [scheme] eval-one-top ()
#5  0x0000000042e02158 in [scheme] call-in-empty-metacontinuation-frame ()
#6  0x0000000043551b07 in [scheme] eval-all ()
...
#13 0x000064e66b2dfa7a in S_call_help ()
#14 0x000064e66b2dfd0d in Scall2 ()
#15 0x000064e66b2d207b in racket_boot ()
```

Frames 0 to 3 are the program's own code, `spin` and `outer`, and the
primitives they call; the frames below them are Racket's REPL and module
machinery.

When the program is attached while it waits, the main thread is usually in
the scheduler, not in your code:

```
(gdb) bt 6
#0  __internal_syscall_cancel (...) at ./nptl/cancellation.c:40
#1  __syscall_cancel (...) at ./nptl/cancellation.c:75
#2  0x00007d22e0d27c0e in __GI___poll (...) at ../sysdeps/unix/sysv/linux/poll.c:29
#3  0x0000633fb6ab3469 in rktio_sleep ()
#4  0x000000004324e312 in [scheme] p ()
#5  0x00000000432dfe38 in [scheme] #f ()
#6  0x00000000431e6dd5 in [scheme] process-sleep ()
#7  0x00000000431e9760 in [scheme] poll-and-select-thread! ()
```

`poll-and-select-thread!` and `process-sleep` mean that no Racket thread is
ready to run, so the scheduler is sleeping until an event arrives.

## A one-shot report from a script

Without starting an interactive session:

```
$ gdb -p $PID -batch -ex 'source chez_gdb.py' -ex 'thread 1' -ex chez-where
pc 0x7d22e0ca03e6 in C: __syscall_cancel + 166 in section .text of /usr/lib/x86_64-linux-gnu/libc.so.6
newest Scheme frame at 0x4324e312 in Scheme code: p
tc 0x633fb6adf2e0, sfp 0x440442d0, current-atomic 0
closure 0x44a8632d: p
  p
  #f
  process-sleep
  poll-and-select-thread!
  call-with-empty-metacontinuation-frame-for-swap
```

When the thread is in C, `chez-where` says where, and then reports from
the newest Scheme frame below it.

## Is a hung program stuck in atomic mode?

A Racket program that ignores Ctrl-C and `kill` (SIGINT and SIGTERM turn
into breaks) and keeps a core busy may be stuck in atomic mode, where the
scheduler never switches threads and breaks are never delivered. Sample
it a few times:

```
for i in 1 2 3 4 5; do
  gdb -p $PID -batch -ex 'source chez_gdb.py' -ex 'thread 1' -ex chez-atomic 2>/dev/null | tail -1
  sleep 1
done
```

A healthy program prints 0 almost every time. The runtime enters atomic
mode briefly all the time, so an occasional 1 means nothing. A program
that prints 1 or 2 in every sample, for seconds, has probably left atomic
mode on. Then look at what it is doing:

```
gdb -p $PID -batch -ex 'source chez_gdb.py' -ex 'thread 1' -ex chez-where
```

A thread spinning in `sync-poll`, `thread-yield` or other scheduler code
with a nonzero count is waiting for another thread that can never run.
The usual cause is code that entered atomic mode and then escaped by an
exception or a continuation jump, skipping its `end-atomic`.

## A poor man's profiler

Attach repeatedly, record which Racket code is running, and count. With
`work.rkt` running:

```
for i in $(seq 1 30); do
  gdb -p $PID -batch -ex 'source chez_gdb.py' -ex 'thread 1' -ex 'chez-stack 1' 2>/dev/null |
    grep '^  ' | head -1 | sed 's/ x[0-9]*$//'
done | sort | uniq -c | sort -rn
```

```
     15   remainder
      6   count-primes
      5   p
      3   fib
      1   exact-integer-sqrt
```

Half the samples are in `remainder`, called from `count-primes`'s inner
loop; `p` is the scheduler sleeping. The `sed` drops the repeat counts that
`chez-stack` adds for recursion (`fib x24`). Each sample stops the program
for about a second, so this suits a program that runs steadily, and more
samples give better numbers. To see callers, print more of the stack
(`chez-stack 3`) and count whole stacks instead of single names.

## Which Racket code calls a C function?

Set an ordinary breakpoint on the C function, and read `bt`:

```
$ gdb -p $PID -ex 'source chez_gdb.py' -ex 'handle all nostop noprint pass'
(gdb) break rktio_sleep
(gdb) continue
Breakpoint 1, 0x00005e093f0733b0 in rktio_sleep ()
(gdb) bt 6
#0  0x00005e093f0733b0 in rktio_sleep ()
#1  0x000000004341e312 in [scheme] p ()
#2  0x00000000434afe38 in [scheme] #f ()
#3  0x00000000433b6dd5 in [scheme] process-sleep ()
#4  0x00000000433b9760 in [scheme] poll-and-select-thread! ()
#5  0x00000000431d29c9 in [scheme] call-with-empty-metacontinuation-frame-for-swap ()
```

The same works for any foreign function: GTK or Cocoa calls from
racket/gui, OpenSSL, SQLite, or your own FFI bindings.

## Break in C only when a given Racket procedure is the caller

`$chez_caller_is(NAME [, N])` is true when one of the `N` newest Scheme
frames (default 20) is named `NAME`:

```
(gdb) break rktio_sleep if $chez_caller_is("process-sleep")
(gdb) continue
```

The condition runs on every hit, so on a very hot function it slows the
program down.

## Break in C only in atomic mode

```
(gdb) break gtk_clipboard_set_with_data if $chez_atomic() > 0
```

racket/gui makes many of its GTK calls in atomic mode, and FFI callbacks
declared with `#:atomic? #t` run in it. A condition like this one finds
the calls made from atomic regions, which are the places where a callback
or a collection can surprise you.

## Count calls and log the first stack

Breakpoint commands can keep counts and print stacks. Put this in
`count.gdb`:

```
set $hits = 0
break rktio_sleep
commands 1
  silent
  set $hits = $hits + 1
  if $hits == 1
    chez-stack 6
  end
  continue
end
```

and run it for two seconds:

```
$ gdb -p $PID -batch -ex 'handle all nostop noprint pass' -ex 'source chez_gdb.py' \
    -x count.gdb \
    -ex 'python import threading; threading.Timer(2.0, lambda: gdb.post_event(lambda: gdb.execute("interrupt"))).start()' \
    -ex continue -ex 'printf "rktio_sleep hits: %d\n", $hits'
  p
  #f
  process-sleep
  poll-and-select-thread!
  call-with-empty-metacontinuation-frame-for-swap
rktio_sleep hits: 187
```

The Python line interrupts the program after two seconds so that the
final `printf` runs.

## Break on a Racket procedure

`chez-break` finds code objects by name and breaks at their entry. `sync`
is a Racket primitive that `work.rkt` calls:

```
(gdb) chez-break sync
sync: code object 0x42e5893f
Breakpoint 1 at 0x42e58980
(gdb) continue
Breakpoint 1, 0x0000000042e58980 in ?? ()
(gdb) chez-stack 4
  sync
  my-loop
  call-with-values
  call-in-empty-metacontinuation-frame
  ... 14 more
```

A breakpoint at the entry catches every call that goes through the
procedure's closure: calls to Racket's primitives from your code, calls
from other modules, and calls through a variable or a higher-order
function. It misses calls that the compiler resolves statically, because
those jump past the entry:

- calls within a module to a procedure defined in that module;
- self-recursion, including loops;
- inlined calls. The compiler inlines small procedures and procedures used
  once, so their code objects may exist but never run.

In `work.rkt`, for example, `chez-break fib` and `chez-break count-primes`
set breakpoints that never hit: `my-loop` calls both directly, and
`count-primes` is inlined. The same holds inside the Racket core, whose
layers call each other directly: `sync-poll`, which only `sync` calls,
never hits.

When a procedure you care about is called directly, break on something
it calls through a closure, such as a primitive or a C function, and use
`$chez_caller_is` to keep only the calls you want.

The collector can move code compiled after startup, and a breakpoint
then points at stale memory. For the Racket core, which never moves,
breakpoints stay valid.

## Procedures with keyword or optional arguments

Racket compiles a procedure with keyword or optional arguments into
several code objects with suffixes such as `.1`. A trailing `*` matches
all of them:

```
(gdb) chez-break sync-poll*
```

## Log every call to a Racket procedure without stopping

Breakpoint commands can print and continue. Put this in `sync.gdb`:

```
set $hits = 0
chez-break sync
commands
  silent
  set $hits = $hits + 1
  if $hits == 1
    chez-stack 4
  end
  continue
end
```

and run it for eight seconds:

```
$ gdb -p $PID -batch -ex 'handle all nostop noprint pass' -ex 'source chez_gdb.py' \
    -x sync.gdb \
    -ex 'python import threading; threading.Timer(8.0, lambda: gdb.post_event(lambda: gdb.execute("interrupt"))).start()' \
    -ex continue -ex 'printf "sync entries: %d\n", $hits'
sync: code object 0x42e5893f
Breakpoint 1 at 0x42e58980
  sync
  my-loop
  call-with-values
  call-in-empty-metacontinuation-frame
  ... 14 more
sync entries: 179
```

To log the caller of every call, drop the `if` and print
`chez-stack 2` each time.

## Catch a crash

A bad pointer in FFI code, or a foreign library that reads freed memory,
raises SIGSEGV. Racket CS turns that into an exception ("invalid memory
reference. Some debugging context lost") or kills the process, and the
native state is lost either way. This program reads from address 16:

```racket
#lang racket/base
(require ffi/unsafe)
(define (read-bad-pointer) (ptr-ref (cast 16 _intptr _pointer) _int))
(define (outer) (+ 1 (read-bad-pointer)))
(outer)
```

Run it under gdb, stopping on SIGSEGV:

```
$ gdb -batch -ex 'source chez_gdb.py' \
    -ex 'handle all nostop noprint pass' -ex 'handle SIGSEGV stop print pass' \
    -ex run -ex 'bt 8' -ex chez-where \
    --args racket crash.rkt

Program received signal SIGSEGV, Segmentation fault.
0x0000000040442108 in ?? ()
#0  0x0000000040442108 in [scheme] [...ad/extest/crash.rkt:5:0 ()
#1  0x000000004251dc7a in [scheme] call-with-values ()
#2  0x00000000424b2158 in [scheme] call-in-empty-metacontinuation-frame ()
#3  0x0000000040441ad6 in [scheme] #f ()
#4  0x0000000042af7386 in [scheme] temp39_0 ()
#5  0x0000000042a3b561 in [scheme] run-module-instance! ()
#6  0x0000000042a35297 in [scheme] perform-require! ()
#7  0x0000000042c02961 in [scheme] namespace-require+ ()
```

The faulting code is named after `crash.rkt:5:0`, the module-level
expression `(outer)`: the compiler inlined `outer` and `read-bad-pointer`
into it. `x/12i $pc-24` shows the faulting instruction, and
`info registers` shows the bad address.

To attach to a program that is already running, use the same `handle`
lines with `-p PID` and `continue` in place of `run`.

When the fault is in a C library, the frames above the Scheme frames show
where:

```
Thread 1 "racket" received signal SIGSEGV, Segmentation fault.
0x00007ec197adc9d4 in g_str_hash () from /lib/x86_64-linux-gnu/libglib-2.0.so.0
#0  g_str_hash ()
#1  g_hash_table_lookup_extended ()
#2  ??? () at /lib/x86_64-linux-gnu/libgdk-3.so.0
#3  gtk_target_list_add_table ()
#4  gtk_selection_add_targets ()
#5  ??? () at /lib/x86_64-linux-gnu/libgtk-3.so.0
#6  gtk_clipboard_set_with_data ()
#7  [scheme] ...
```

(trimmed: addresses and library paths removed)

## Catch a crash that happens once in many runs

For a crash that shows up once in a hundred runs, run many copies, each
under its own gdb that stops on the signal. A gdb that is only waiting for
a signal costs almost nothing.

```
#!/bin/bash
# run-and-catch.sh N: run N copies of a test under gdb; keep a report of any crash
for i in $(seq 1 "$1"); do
  gdb -batch -ex 'source chez_gdb.py' \
    -ex 'handle all nostop noprint pass' \
    -ex 'handle SIGSEGV stop print pass' -ex 'handle SIGBUS stop print pass' \
    -ex run \
    -ex 'info registers' -ex 'x/24i $pc-48' -ex 'bt 40' -ex chez-where \
    -ex 'thread apply all bt 15' \
    --args racket my-test.rkt > gdb-$i.txt 2>&1 &
done
wait
grep -l 'received signal SIG' gdb-*.txt
```

With `crash.rkt` from the previous example as the test:

```
$ ./run-and-catch.sh 3
gdb-1.txt
gdb-2.txt
gdb-3.txt
$ grep -h -m1 '^#0' gdb-1.txt
#0  0x0000000040442108 in [scheme] [...ad/extest/crash.rkt:5:0 ()
```

When a copy exits normally, gdb reports that, the remaining commands fail
harmlessly, and gdb exits. Starting each copy under gdb catches a crash
at any time, even one in the first moments. Attaching to a running copy
takes about a second, so a crash before then would be missed.

To catch hangs instead, run the copies through `allow-ptrace`, and after
a time limit attach to any copy that is still running and run
`chez-where`.

## Look at every thread, including places

Racket places run on their own OS threads:

```
(gdb) thread apply all bt 6
Thread 2 (Thread 0x7455043ff6c0 (LWP 287492) "racket"):
#0  0x00000000402fde24 in [scheme] spin-in-place ()
#1  0x0000000043138ff9 in [scheme] proc ()
#2  0x0000000042f36158 in [scheme] call-in-empty-metacontinuation-frame ()
#3  0x0000000042f36158 in [scheme] call-in-empty-metacontinuation-frame ()
#4  0x0000000042fbf7af in [scheme] #f ()
#5  0x0000000042f369c9 in [scheme] call-with-empty-metacontinuation-frame-for-swap ()
Thread 1 (Thread 0x745504dbb100 (LWP 287490) "racket"):
...
#5  0x0000000043182312 in [scheme] p ()
...
```

Here thread 2 is a place running `spin-in-place`, and thread 1 is the main
place waiting in the scheduler. The other threads belong to the C runtime
(its garbage collector's sweepers and signal handling) or to libraries
such as GLib, and they have no Scheme frames.

Only the Racket thread that is running on each OS thread is visible. A
Racket thread that is waiting has its continuation saved in its engine,
not on a stack.

## Post-mortem from a core file

Take a core of a running program with `gcore`, and debug it later, even
on a machine where you cannot attach:

```
$ gcore -o core $PID
$ gdb -batch -ex 'source chez_gdb.py' -ex 'bt 12' -ex chez-where racket core.$PID
#3  0x0000633fb6ab3469 in rktio_sleep ()
#4  0x000000004324e312 in [scheme] p ()
#5  0x00000000432dfe38 in [scheme] #f ()
#6  0x00000000431e6dd5 in [scheme] process-sleep ()
#7  0x00000000431e9760 in [scheme] poll-and-select-thread! ()
#8  0x00000000430029c9 in [scheme] call-with-empty-metacontinuation-frame-for-swap ()
#9  0x0000633fb69e8a7a in S_call_help ()
...
newest Scheme frame at 0x4324e312 in Scheme code: p
tc 0x633fb6adf2e0, sfp 0x440442d0, current-atomic 0
```

Everything works on a core except `chez-break`, which needs a live
process, and the fallback that asks the process for its thread context,
which the tool needs only when the registers do not hold Scheme's values.

## Name an address

`chez-name` names the code object containing an address, such as a
program counter from a log, a register or a return address found in
memory:

```
(gdb) chez-name $pc
0x40a45524 is remainder+388 (code object 0x40a4535f)
(gdb) chez-name rktio_sleep+20
0x556e192523c4 is not in Scheme code: rktio_sleep + 20 in section .text of .../racket/bin/racket
```

In expressions, `$chez_name(ADDR)` gives the same name as a string:

```
(gdb) printf "%s\n", $chez_name($pc)
remainder
```

## Map a generated name back to source

Frames in the Racket core often have names that schemify generated, such
as `temp50_0` or `temp39_0`. A Racket CS build tree keeps the schemified
core in `racket/src/cs/schemified/`:

```
$ grep -n 'temp50_0' racket/src/cs/schemified/*.scm
racket/src/cs/schemified/thread.scm:10334:     (let ((temp50_0
racket/src/cs/schemified/thread.scm:12494:     (let ((temp50_0 (future*-id f6_0)))
racket/src/cs/schemified/expander.scm:19639:   (let ((temp50_0 (syntax-e$1 id12_0)))
racket/src/cs/schemified/expander.scm:28216:   (let ((temp50_0
```

Only the first of these is a procedure in the thread layer, and reading
the code around line 10334 shows it:

```scheme
(let ((temp50_0
       (lambda (sched-info_0 now-polled-all?_0 no-wrappers?_0)
         (begin
           (if timeout-at_0
             (schedule-info-add-timeout-at! sched-info_0 timeout-at_0)
             (void))
           (thread-yield sched-info_0)
           (loop_0 ...
```

That is the `#:fail-k` callback of the polling loop in `sync`, in
`racket/src/thread/sync.rkt`. The names of the frames next to a generated
one usually settle which definition it is.

## Debug a different Racket build

The tool reads Chez's layout constants from the build's `equates.h`,
found relative to the racket binary. For an installed Racket without its
build tree, point it at the `equates.h` of a build of the same version:

```
(gdb) chez-layout ~/src/racket/racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h
layout from /home/me/src/racket/racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h
```

With no argument, `chez-layout` says which layout is in use:

```
(gdb) chez-layout
layout from built-in values for Racket CS 9.3.0.8
```
