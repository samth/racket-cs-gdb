# racket-perf

Record a Linux perf profile of a Racket CS program, name its Scheme procedures,
and save a readable report alongside the recording. Implemented in Racket;
Python and GDB are not required.

Requires x86_64 Linux, Racket CS, and Linux perf with the `--control` interface.
The system must allow perf recording and tracing your own child process.

From a checkout:

```sh
raco pkg install --auto --link /path/to/racket-cs-gdb/racket-perf
raco racket-perf --duration 10 -o my-profile my-program.rkt arg1 arg2
raco docs racket-perf
```

For a rackup toolchain, prefix the installation and profiling commands with
`rackup run TOOLCHAIN --`. The command profiles the Racket running `raco` and
preserves its package environment.

The installed manual is generated from
[scribblings/racket-perf.scrbl](scribblings/racket-perf.scrbl). It covers all
options, saved artifacts, exit and interrupt behavior, permissions, and symbol
map limitations. `raco racket-perf --help` prints the option summary.

## Profiling without perf: the built-in sampler

`racket-perf/sampler` uses the stack sampler built into Chez Scheme (branch
`perf-stack-sampler` of samth/ChezScheme), which interrupts the program on a
CPU-time clock and walks Chez's own frame metadata. It needs no perf, root, or
frame pointers, works on x86_64 and arm64, and sees whole stacks through
continuations and Racket's metacontinuations, plus C and collector time.

```racket
(require racket-perf/sampler)
;; like `profile-thunk` from the profile library, with its text renderer
(profile-thunk/sampler (lambda () (work)) #:folded "work.folded")
```

`#:folded` also saves folded stacks for `flamegraph.pl --colors java` (program
code green, runtime aqua, C yellow), speedscope, or inferno. By default stacks
are tidied: process and module start-up, `dynamic-wind`, `call-with-values`,
continuation plumbing, and anonymous runtime thunks are dropped, and remaining
anonymous procedures are named after their source, caller, or module. Pass
`#:tidy? #f` for raw stacks. `sampler-start!`, `sampler-stop!`, and
`sampler-take!` give direct control, and `tidy-stacks` and `samples->profile`
work on samples from any source, including a `CHEZ_SAMPLE_STACKS` file read
with `read-folded`.

Racket threads share one OS thread, so all of them are sampled; other OS
threads (places, futures, parallel threads) are sampled only if they start
while sampling is on.

## Package catalog source

This directory is the complete package; the repository root contains the
separate GDB integration. After publishing this directory to the repository,
register package name `racket-perf` with this source:

```text
https://github.com/samth/racket-cs-gdb.git?path=racket-perf
```

The package includes its command registration, implementation, Scribble manual,
Racket tests, standalone `bin/racket-perf` launcher, and both license texts.
Its runtime dependencies are `base` and `profile-lib`; documentation and tests use the
build dependencies declared in `info.rkt`.
