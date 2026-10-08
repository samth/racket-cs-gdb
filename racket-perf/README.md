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

## Package catalog source

This directory is the complete package; the repository root contains the
separate GDB integration. After publishing this directory to the repository,
register package name `racket-perf` with this source:

```text
https://github.com/samth/racket-cs-gdb.git?path=racket-perf
```

The package includes its command registration, implementation, Scribble manual,
Racket tests, standalone `bin/racket-perf` launcher, and both license texts.
It has only `base` as a runtime dependency; documentation and tests use the
build dependencies declared in `info.rkt`.
