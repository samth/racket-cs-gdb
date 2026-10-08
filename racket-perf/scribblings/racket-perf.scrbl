#lang scribble/manual
@(require (for-label racket/base))

@title[#:tag "racket-perf"]{racket-perf: Racket CS Perf Profiling}
@author{Sam Tobin-Hochstadt}

The @tt{racket-perf} package provides the @exec{raco racket-perf} command.
It runs a Racket CS program under Linux perf, names the machine code generated
by Chez Scheme, and saves a recording and a readable report. It prints the
hottest procedures with sample counts and percentages. The implementation is
in Racket and requires neither Python nor GDB.

@section{Requirements and Installation}

The profiler supports x86_64 Linux and Racket CS. Install Linux perf with its
@tt{--control} interface; perf 7.0.14 and Racket CS 9.3.0.10 were used for
validation. Linux must permit perf sampling and tracing your own child process.
The profiler uses ptrace to stop the child at exec, signal delivery, and exit;
it does not step through instructions.

Once the package is available in your catalog, install it with:
@verbatim{
raco pkg install racket-perf
}

To install the package directory from a repository checkout:
@verbatim{
raco pkg install --auto --link /path/to/racket-cs-gdb/racket-perf
}

With rackup, install it into the toolchain that will run the profiler:
@verbatim{
rackup run local-plt -- raco pkg install --auto --link /path/to/racket-cs-gdb/racket-perf
}

The command profiles the Racket executable running @exec{raco}, using its
existing environment and package directories. Use @tt{--racket PATH} to select
another executable; that option retains the current environment. To select
another toolchain and its environment together, use @exec{rackup run}.

@section{Running a Profile}

@verbatim{
raco racket-perf my-program.rkt arg1 arg2
rackup run local-plt -- raco racket-perf -o my-profile my-program.rkt arg1 arg2
}

Program output passes through to the terminal. Scripts run normally, including
their @racket[module+ main] submodules. Options for the profiler go before the
script; arguments after the script are passed to it unchanged, even if they
look like profiler options. Use @tt{--} before flags for Racket itself:

@verbatim{
raco racket-perf --duration 10 --unique -o my-profile my-program.rkt
raco racket-perf --duration 10 -- -l drracket
raco racket-perf -- -e '(for ([i (in-range 1000000)]) (sqrt i))'
}

Perf is enabled before the program starts, so the recording includes startup.
Scheme symbols are captured before the process exits or handles a requested
interrupt. A short program can therefore still produce a Scheme map.

@section{Command Options}

@itemlist[
 @item{@tt{-o DIR}, @tt{--output DIR}: create a new result directory. The default
       is a timestamped @tt{racket-perf-...} directory in the current directory.
       An existing directory is never overwritten.}
 @item{@tt{-F N}, @tt{--frequency N}: request a positive integer sampling
       frequency in samples per second. Default: @tt{999}.}
 @item{@tt{-e EVENT}, @tt{--event EVENT}: select the perf event.
       Default: @tt{cpu-clock:u}, which samples user-space CPU time.
       Event availability and permissions depend on the machine.}
 @item{@tt{--duration SECONDS}: stop the program after a positive, finite
       recording duration. The limit starts when perf is enabled. This excludes
       profiler setup and final map/report generation.}
 @item{@tt{--racket PATH}: profile an explicit Racket executable, instead of
       the Racket running the command.}
 @item{@tt{--equates PATH}: read the matching Chez @tt{ta6le/equates.h} layout
       constants. Otherwise the profiler searches the build tree around the
       target executable, then falls back to built-in values for CS 9.3.0.8.
       Use a matching file when profiling another object layout.}
 @item{@tt{--unique}: append each code object's address to its Scheme name,
       keeping different objects with the same name separate in the report.}
 @item{@tt{--rows N}: print at most this many positive-integer report rows.
       Default: @tt{20}. The saved text report contains all rows.}
 @item{@tt{--interactive}: open perf's interactive report after saving the
       complete text report.}
 @item{@tt{-h}, @tt{--help}: print the command's option summary.
       @exec{raco help racket-perf} also shows it.}
]

Long value options accept @tt{--option=value} as well as
@tt{--option value}. For example:
@verbatim{
raco racket-perf -F 499 -e cycles:u --equates /path/to/ta6le/equates.h \
    --interactive my-program.rkt
}

@section{Saved Results}

Every recording creates these files in its result directory:

@itemlist[
 @item{@tt{perf.data}: the perf recording, including sample counts and addresses.}
 @item{@tt{report.txt}: the complete flat profile, grouped by shared object and
       symbol, with sample counts and percentages.}
 @item{@tt{perf-PID.map}: the snapshot of named Chez code objects for the
       profiled process. The actual file name contains its process ID.}
 @item{@tt{perf.log}: perf's output, including control and recording diagnostics.}
 @item{@tt{run.json}: the command and arguments, sampling settings, toolchain,
       process ID, layout source, map object count, timing, and exit status.
       It also records interruption and errors when available.
       Environment variables are not included.}
]

The map is also published at @filepath{/tmp/perf-PID.map}, which is where perf
looks for these symbols. Reopen a recording with:
@verbatim{
perf report -i my-profile/perf.data
}

If the temporary map has been removed, copy the saved map back to @filepath{/tmp}
first, keeping its original @tt{perf-PID.map} file name. The saved
@filepath{report.txt} remains readable without that temporary map.
Short procedure names such as @tt{p}, @tt{go}, and @tt{#f} are bracketed in
ordinary maps so perf recognizes them; @tt{--unique} uses an address suffix.

@section{Stopping and Exit Status}

Ctrl-C forwards an interrupt to the profiled program and saves the map and
report before returning a nonzero interrupted status. SIGTERM is also forwarded.
A duration limit sends SIGTERM when recording time expires. If the program
has not exited after three seconds, the profiler sends SIGKILL.

A completed duration-limited recording returns success. An ordinary run returns
the program's exit status; signal termination is represented as @tt{128} plus
the signal number. A profiler or perf failure returns a nonzero status. Inspect
@filepath{perf.log} and @filepath{run.json} for failure details; not every failed
run can produce a map or report.

@section{Reading the Profile and Its Limits}

The report is a flat profile of the main Racket process and its OS threads.
It does not supply Scheme call graphs, and separate subprocesses do not get
their own Scheme maps. High sample counts identify where CPU time was observed;
startup and runtime work can appear alongside procedures from your program.

The Scheme map is a snapshot at capture time. Code moved or collected earlier
in the run can leave historical samples unresolved or inaccurately named.
@tt{--unique} distinguishes code objects in that snapshot; it does not track
objects through garbage collection. A name of @tt{#f} means the code object has
no Scheme string name.

For permission failures, confirm that the same user can record with perf and
trace a child process under the machine's security policy. For missing Scheme
names, check the recorded layout source and use a matching @tt{--equates} file.
A successful map cannot resolve code that was already collected before capture.

@section{Standalone Launcher}

A checkout also provides @filepath{bin/racket-perf}, which uses the same
implementation. When rackup is available, it selects the current toolchain and
obtains its environment through @exec{rackup run}; otherwise it uses Racket
from @tt{PATH}. An explicit @tt{--racket} retains the caller's environment.
Installing the package and using @exec{raco racket-perf} is the normal entry point.
