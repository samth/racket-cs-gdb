# Intel PT tools for Chez Scheme and Racket CS: an evaluation

huor (Intel Core Ultra 7 265: P-cores 0-7, E-cores 8-19), Linux 7.0, perf 7.0.14,
unprivileged (`perf_event_paranoid` = 0, `perf_event_mlock_kb` = 516), 2026-11-01.

## Summary

- Intel PT can rebuild exact Chez and Racket CS call stacks, but only with help from
  Chez: a jitdump with TSC timestamps that covers boot code (to name and decode Scheme
  code) and a decoder that knows Chez's `jmp`-based calls and returns. Stock tools
  (`perf script --call-trace`, `--itrace=...g` callchains, stock magic-trace) see flat
  Scheme stacks: every Scheme call looks like a tail call.
- I patched magic-trace (v1.2.4, built from source) with a Chez mode. On fib(20) it
  counts every call (21891 entries of `fib`, nesting 20) and roots 100% of fib time at
  `Sscheme_start;...;ksrc;compile;map`. On a Racket compile (`managed-compile-zo` of
  racket/list.rkt) its user-time profile matches the in-process sampler: over Scheme
  procedures, self-time overlap 0.89 and inclusive overlap 0.86 (1.0 = identical), and
  `compile-root` is on the stack 58.0% of the time (PT) vs 56.9% (sampler). perf's own
  PT callchains score 0.04 on inclusive overlap.
- Three things made the difference: keeping the reconstructed stack across decode
  errors (stock magic-trace clears it), matching returns by return address, and
  switching to a saved stack when a jump returns to an address that a frame was
  waiting for earlier. Racket's engine swaps and continuations do that constantly, and
  so do the stacks lost to decode errors.
- Practical limits, all from the unprivileged 512 KB AUX buffer and the decoders:
  - full traces lose 5-9% of AUX chunks even on a quiet machine, run at 240-400 MB/s
    of trace, and take 900-1600 s to decode per second of traced execution;
  - snapshot mode captures only the last ~190k branches (about 2 ms of compiler
    execution, 0.2 ms with cycle-accurate timing);
  - E-cores overflow 10-30 times more than P-cores; use a P-core;
  - the mlock limit is per user, so a second concurrent PT recording fails silently;
  - full tracing slows kernel-heavy phases (page faults, GC) a lot: GC time under
    full tracing was 3x the untraced GC log.
- Recommendation: keep the in-process sampler (+2.5% at 999 Hz) as the profiler. Use
  PT, with magic-trace's Chez mode, when the question is about one specific stretch of
  execution: the exact call sequence, how long each GC or call took, or why one
  operation was slow. Pin the program to a P-core, give perf its own core, and keep
  runs under about a second.

## Setup

Builds (all under /tmp/mtrace):

| build | what |
|---|---|
| `prof-tsc` (Chez, `perf-profiling` @ c1021498 + jitdump changes) | sampler + jitdump; binary saved as `bin/scheme-tsc-v2` |
| `racket` (Racket CS 9.4.0.3, racket master 437a2611 + the same Chez patches) | `make base`, then `raco pkg install drracket` |
| `magic-trace-src` (magic-trace v1.2.4 + perf-7 fix + Chez mode) | OCaml 5.2.1, Jane Street v0.17, private opam root |

The Chez patches are now samth/ChezScheme `perf-profiling` @ 840b1a2d. Relative to
c1021498 the jitdump: (1) stamps records with the TSC when `CHEZ_JITDUMP_TSC=1` and
sets `JITDUMP_FLAGS_ARCH_TIMESTAMP`, which `perf inject --jit` requires for an
`intel_pt` recording (perf cannot combine `-k mono` with intel_pt); (2) opens the dump
before the boot files load, gets records for code created and moved during boot, and
calls the hooks from `Scompact_heap` (boot loading compacts the heap between boot
files without them, which left stale pending pointers and crashed); (3) names code as
the sampler does (`name@file:line`), so PT and sampler profiles use the same names.
Chez `make test-one` (o=3) passed with jitdump on in every mat process.

Workloads:

| workload | command | untraced wall |
|---|---|---|
| fib20 | Chez: `(fib 20)` from a source file, so the run includes boot and compile | 0.07 s |
| cp0 | Chez: `compile-file` of s/cp0.ss at o3, loading the build's own compiler .so files | 0.57 s idle, 1.2 s with decodes running |
| rkt-load | `racket -l racket -e 1` | 0.46 s |
| rkt-list | `racket -l racket/base -l compiler/cm -e '(managed-compile-zo "list.rkt")'` on a copy of racket/list.rkt | 0.58 s |
| drracket | DrRacket startup under xvfb-run, exiting when the first frame is shown | 4.0 s |
| compiler bench (overhead only) | cp0.ss, cpprim.ss, cpnanopass.ss at o3, timed in-process | 5.0 s |

Tools compared:

- **mt** — patched magic-trace, Chez mode (`MAGIC_TRACE_CHEZ=1`): `v4` keeps stacks
  across errors; `v6` adds saved-stack switching (the final version).
- **py** — `chez_pt_stacks.py` (same rules as v4, without stack switching).
- **perf** — `perf script --itrace=i100usg` (perf's own call/ret-based callchains,
  sampled every 100 µs of trace).
- **sampler** — Chez's in-process sampler (`CHEZ_SAMPLE_STACKS`, 10 kHz for the
  comparisons, `CHEZ_SAMPLE_MARK=0`), 10-20 runs pooled.

Recordings for the comparisons: `pt-record.sh`, program pinned to P-core 2, perf
pinned to P-core 0, `intel_pt/noretcomp=1/u`, `-m 8,512K`, full execution, nothing else
recording. Decoding ran on E-cores 8-19.

## How the Chez reconstruction works

Chez on x86_64 never uses `call`/`ret` for Scheme:

- non-tail call: `lea ret(%rip),%rcx; mov %rcx,0(%r13); jmp target`
- tail call: `jmp target` with no store
- return: `jmp *0(%r13)`, or `jmp *%r12` / `jmp *-0x20(%r12)` for single and multiple
  values

With the jitdump's code bytes (looked up by the `jitted-PID-INDEX.so` that
`perf inject --jit` assigns to each branch), Chez mode classifies each `jmp`:

1. any jump to an address that a frame on the stack is waiting for (its return
   address, or its mv-return address at ret-0x20) pops to that frame, which covers
   escapes and C `ret`s back into Scheme;
2. an indirect jump or C `ret` to an address that no current frame waits for, but that
   a frame waited for earlier, switches to the stack saved when that frame was pushed
   (v6). Frames are an immutable list, so saving costs one hash-table store per call;
3. a `jmp *0(%r13)` that matches nothing pops one frame (resync);
4. a jump preceded by the `lea`/`mov` pair pushes a frame;
5. any other jump to a code entry renames the top frame (tail call);
6. real `call`s push with return address ip+ilen.

The reconstruction keeps its own copy of the writer's stack and emits only Call,
Return and Jump events, so the rest of magic-trace (Perfetto output, timing) works
unchanged. Decode errors no longer clear the stack; a `tr strt` after an error becomes
a jump. `MAGIC_TRACE_CHEZ_FOLDED=file` also writes folded stacks weighted by trace
time.

Other magic-trace fixes: perf 7 prints `tr strt jmp` and `tr end  jmp`, which stock
v1.2.4 cannot parse (upstream fixed only the first); the .fxt writer crashed when a
stack's reset time ran a few ns past the event that used it; `perf inject -o` renames
an old output to `*.old`, which magic-trace then decoded a second time.

## Correctness

### fib20 (exact answers known)

| | mt v4 | mt v6 | py | perf `--itrace=i100usg` | stock magic-trace (Oct 31) |
|---|---|---|---|---|---|
| fib entries (exact: 21891) | 21891 | 21891 | 21891 | n/a (fib flat under S_call_help) | fib appears once |
| deepest fib nesting (exact: 20) | 20 | 20 | 20 | 1 | 1 |
| fib time rooted at `Sscheme_start` | 0% | 100% | 0% | — | — |
| unmatched `jmp *0(%r13)` (resync) | 50 | 26 | 50 | | |
| unmatched C `ret` | 327 | 2 | 327 | | |
| returns to a saved stack | — | 238 + 295 C | — | | |
| decode errors | 2341 | 2341 | 2341 | | 2002 (snapshot) |
| Scheme events at depth 0-1 | 0.0% | 0.0% | | | 97% |

mt v4 and py agree on every count (both use the same rules). Before v6, fib stacks
were complete from `fib` up through `ksrc;compile;map` but lost the frames below, so
no stack reached `Sscheme_start`.

### Agreement with the sampler

Each PT profile is compared with the sampler profile of the same workload: shares of
self time (leaf) and inclusive time per procedure name; the overlap score is the sum
over names of min(PT share, sampler share), 1.0 for identical profiles. PT profiles
weigh wall time between branches, including kernel time (shown as `[untraced]` and
`[syscall]`), which the sampler does not see the same way, so PT stacks whose leaf is
kernel time are dropped (`--user-only`), as are single stacks heavier than 5% of the
total (timestamp jumps, below).

| workload | tool | weight dropped (kernel leaf / gap) | self overlap | inclusive overlap |
|---|---|---|---|---|
| rkt-list | mt v6 | 64.1% / 0 | **0.779** | **0.568** |
| rkt-list | mt v4 | 64.1% / 0 | 0.779 | 0.354 |
| rkt-list | py | 10.8% / 0 | 0.507 | 0.320 |
| rkt-list | perf i100usg | 0.1% / 0 | 0.656 | 0.058 |
| rkt-load | mt v6 | 99.3% (one 22 s gap) | 0.651 | 0.438 |
| rkt-load | mt v4 | 99.3% | 0.651 | 0.582 |
| rkt-load | py | 97.4% | 0.297 | 0.454 |
| rkt-load | perf i100usg | 0 | 0.523 | 0.098 |
| cp0 | mt v4 | 59.1% / 15.9% | 0.721 | 0.478 |
| cp0 | mt v6 | 59.1% / 15.9% | 0.721 | 0.437 |
| cp0 | perf i100usg | 0 / 5.2% | 0.645 | 0.088 |

(py does not mark kernel time, so its kernel time lands on user leaves and lowers its
self overlap. The py decode of cp0 was killed for running out of memory: its folded
keys for 4000-frame stacks are too big.)

rkt-list, top inclusive shares (mt v6 / sampler):

```
  92.80%  89.91%  [anonymous]
  90.53%  87.95%  call-in-empty-metacontinuation-frame
  64.76%  63.30%  eval-all
  58.03%  56.89%  compile-root@cm-minimal.rkt:221
  57.14%  56.81%  compile-zo*/cross-compile@cm-minimal.rkt:647
  51.90%  50.77%  compile
```

Before v6 (mt v4), `compile-root` had 7.3% and `eval-all` 0.4%: stacks broken by
engine swaps and decode errors never recovered their outer frames. perf's own
callchains get self time roughly right (they come from IPs) but have no Scheme nesting
(inclusive overlap 0.06-0.10, max depth 19-22, almost all C frames).

rkt-list, top self shares (mt v6 / sampler): `sweep_generation_pass` 13.4/14.9%,
`copy` 4.2/5.2%, `S_vfasl` 3.4/4.6%, `S_intern_sc` 3.8/4.2%, `loop` 2.3/2.4%,
`accessor` 1.7/1.9%. PT names `__memmove_avx_unaligned_erms` (6.2%), which the sampler
reports only as `libc.so.6+0x197d07` (2.3%) and similar addresses.

### Remaining stack errors

| workload | resyncs (pop one) | returns to a saved stack | unmatched C ret | decode errors |
|---|---|---|---|---|
| fib20 | 26 | 533 | 2 | 2341 |
| rkt-list | 46082 | 32282 | 12 | 16979 |
| rkt-load | 25045 | 22709 | 13 | 13747 |
| cp0 (v4) | 53654 | — | 2368 | 24244 |
| cp0 (v6) | 39948 | 15934 | 27 | 24244 |

Resyncs are about 0.3% of the 16.8M non-tail calls in rkt-list. cp0 also shows a
stack leak: 4070 frames deep, 3630 of them `float` in a row (`float` really does
recurse 344 deep in fib20 and rkt-load, so part of this is genuine), and v6 does not
change it.

cp0, top inclusive shares (mt v6 / sampler): `invoke` 91.7/91.6%, `ksrc` 91.0/91.3%,
`dynamic-wind` 91.2/93.2%, `map` 66.6/75.5%, but `do-compile-file` 59.9/90.4% (v4:
4.6%). So v6 restores the outer frames of the compiler too, but a third of the time
under `do-compile-file` still sits on a stack that lost that frame. The inclusive
overlap scores above count C frames (`main`, `boot_call`, ...) that only PT sees;
restricting both profiles to Scheme procedure names (frames the sampler shows as
Scheme; `--scheme-only`) gives a fairer inclusive score:

| workload | mt v6 self / inclusive | mt v4 | py | perf i100usg |
|---|---|---|---|---|
| rkt-list | **0.891 / 0.858** | 0.885 / 0.336 | 0.782 / 0.325 | 0.815 / 0.039 |
| rkt-load | **0.862 / 0.802** | | | 0.786 / 0.064 |
| cp0 | **0.796 / 0.783** | 0.795 / 0.465 | (OOM) | 0.719 / 0.063 |

Self time is right in every PT tool, since it only needs the IP; nesting needs the
reconstruction, and only v6 recovers most of it.

### Timing artifacts

Full-trace timestamps are not reliable for long gaps. The rkt-load trace contains one
22 s forward jump in perf's decoded timestamps near the end of a 1 s run (inside a GC,
in `S_jitdump_code_moved`); cp0 has three stacks of 168-245 ms attributed to kernel
addresses and one odd symbol (`unsigned_short`). These are kernel time (PT-buffer
interrupts, page faults in the collector) and decoder timing errors charged to the
last user-space location. Any time-weighted PT profile needs this filter.

## What PT shows that sampling does not

- **Exact call counts and sequences.** fib's 21891 calls; for rkt-list, 16.8M
  non-tail calls, 6.7M tail calls, and the top entries (`accessor` 2.34M, `loop` 1.55M,
  `stencil-vector-copy!` 850k, `hash-ref/none` 594k). The .fxt traces open in
  ui.perfetto.dev with every call as a slice.
- **Per-call latency.** Every GC in the rkt-list compile: 31 `S_do_gc` calls, median
  4.5 ms, p90 33 ms, max 67 ms, total 315 ms (chez_pt_stacks.py `--durations`). The
  untraced run's GC log (`PLTSTDERR=debug@GC`) shows 29 GCs, median 2 ms, max 24 ms,
  total 100 ms: PT gets the count and shape right but full tracing inflates GC time
  about 3x, because GC is page-fault and memory heavy.
- **Recursion depth.** The deepest non-tail self-recursion per procedure, e.g. `map` 528
  and `float` 395 in rkt-list, `create-the-variables` 266 in rkt-load.
- **Names the sampler lacks**: libc symbols (`memmove`, 6% of rkt-list user time,
  which the sampler shows as raw libc offsets), and the boot-time code that runs while
  boot files load, now named because the jitdump covers it.

## Cost

### Overhead (compiler bench, program on P-core 1, 7 rounds, nothing else of mine running)

| config | median elapsed | min-max | vs none |
|---|---|---|---|
| none (frame-free baseline, prof build) | 4964 ms | 4857-5067 | — |
| jitdump on (`CHEZ_JITDUMP` + TSC) | 4946 ms | 4848-5050 | -0.4% (noise) |
| sampler, 999 Hz | 5086 ms | 4904-5138 | +2.5% |
| perf PT snapshot, `noretcomp` | 6241 ms | 6181-6370 | +25.7% |
| perf PT snapshot, `noretcomp,cyc=1,cyc_thresh=1,mtc_period=0` | 6694 ms | 6609-6747 | +34.9% |
| perf PT full, perf on E-cores 8-19 | 5603 ms | 4800-6493 | +12.9% (9.15 GB, data dropped) |
| perf PT full, perf on P-core 0 (3 rounds) | 5349-6206 ms | | +7% to +25% (1.9-6.3 GB, varies with loss) |
| magic-trace Chez mode, snapshot (default Normal timing) | 6698 ms | 6566-6770 | +34.9% |

jitdump itself costs 10-20 ms at Racket startup (`racket -n`: 0.05 s → 0.06-0.07 s)
and writes 22 MB per process for Racket's boot code (30 MB for Chez fib20).

Full tracing loses data instead of slowing the program much: when perf cannot drain
the 512 KB buffer, the hardware drops trace and the program runs on, so the run-to-run
spread is large. Snapshot mode never drains, so it pays the full tracing cost
(+26-35%).

Full-trace runs also stretch kernel-heavy phases: DrRacket startup took 8.0 s traced
vs 4.0 s untraced.

### Trace size, data loss, decode time (quiet full recordings, P-core 2, perf on P-core 0)

| workload | perf.data | trace time | trace MB/s | AUX chunks lost | decode errors | mt v6 decode | py decode | decode s per traced s |
|---|---|---|---|---|---|---|---|---|
| fib20 | 48 MB | 0.13 s | 370 | 23/425 (5.4%) | 2341 | 176 s | 97 s | ~1360 |
| cp0 | 639 MB | 1.54 s | 415 | 534/5696 (9.4%) | 24244 | 2677 s (v4: 2495 s) | killed (OOM) | ~1740 |
| rkt-load | 252 MB | ~1.0 s | 250 | 81/2834 (2.9%) | 13747 | 1122 s | 562 s | ~1120 |
| rkt-list | 370 MB | 0.88 s | 420 | 168/3428 (4.9%) | 16979 | 1629 s | 889 s | ~1850 |
| drracket | 2445 MB | ~8 s | ~300 | 1117/25018 (4.5%) | (decode running) | | | |

**Decoding is the binding constraint: roughly 20-30 minutes of single-threaded decode
per second of traced execution.** About half of that is `perf script` itself (the
Python tool, which reads the same `perf script` output, takes 55-60% of magic-trace's
time). perf's sampled callchain mode (`--itrace=i100usg`) decodes the same cp0 trace in
41 s, but without Scheme stacks.

Error types (quiet recordings): overflow packets dominate (fib20 2272 of 2341, cp0
23388 of 24244, rkt-load 13523 of 13747, rkt-list 16593 of 16979); then lost trace data
(one per lost AUX chunk), 29-202 "trace doesn't match instruction", and a few dozen
bad packets and failed instruction reads. Hardware overflow (the core producing
trace faster than it can write it, even with the buffer drained) is distinct from AUX
loss (perf not draining the buffer in time); both happen in every full trace here.

### Snapshot window

magic-trace's default (and perf `--snapshot`) keeps a ring buffer of the last 512 KB
(the unprivileged maximum; root could use 4 MB+). On fib20 that held 193k branches
with `noretcomp` and default timing, 248k with return compression, and 89k with
cycle-accurate timing (`cyc=1`, magic-trace's Normal and High). At the ~100M
non-conditional branches per second that the cp0 compile executes, one capture covers
about 2 ms of compiler execution, or 0.2 ms with cycle-accurate timing. Snapshots
almost never overflow on a P-core (18-29 overflow packets on fib20).

## PT settings and cores (fib20; `-m 8,512K`)

Counts are decode errors in one run; "fib" is the number of decoded entries to `fib`
(exact 21891).

P-core 2:

| mode | config | MB | overflow | mismatch | lost | AUX lost | fib |
|---|---|---|---|---|---|---|---|
| full | default (return compression) | 21 | 8 | 52 | 15 | 15/317 | **10909** |
| full | noretcomp | 23 | 649 | 5 | 10 | 10/334 | 21891 |
| full | noretcomp,mtc=0 | 18 | 535 | 7 | 19 | 19/239 | 21891 |
| full | noretcomp,mtc=0,psb_period=5 | 25 | 1159 | 9 | 12 | 12/360 | 21891 |
| full | noretcomp,mtc=0,psb_period=5,branch=1 | 45 | 2039 | 11 | 2 | 2/720 | 21893 |
| full | noretcomp,cyc=1,cyc_thresh=1,mtc_period=0 | 72 | 2545 | 1 | 5 | 5/1155 | 21893 |
| snapshot | default | 0.5 | 2 | 0 | 0 | 0 | 21891 |
| snapshot | noretcomp | 0.5 | 19 | 0 | 0 | 0 | 21891 |
| snapshot | noretcomp,mtc=0 | 0.5 | 18 | 0 | 0 | 0 | 21891 |
| snapshot | noretcomp,mtc=0,psb_period=5 | 0.5 | 20 | 13 | 0 | 0 | 20984 |
| snapshot | noretcomp,cyc=1,cyc_thresh=1,mtc_period=0 | 0.5 | 29 | 0 | 0 | 0 | 21891 |

E-core 12:

| mode | config | MB | overflow | mismatch | lost | AUX lost | fib |
|---|---|---|---|---|---|---|---|
| full | default | 22 | 4454 | 1371 | 0 | 0 | 15345 |
| full | noretcomp | 35 | 21128 | 30575 | 4 | 4/558 | 15264 |
| full | noretcomp,mtc=0 | 35 | 20619 | 29578 | 2 | 2/557 | 15695 |
| full | noretcomp,mtc=0,psb_period=5 | 33 | 15972 | 21603 | 3 | 3/514 | 7810 |
| full | noretcomp,cyc=1,cyc_thresh=1,mtc_period=0 | 52 | 74938 | 26932 | 7 | 7/805 | 11490 |
| snapshot | default | 0.5 | 653 | 468 | 0 | 0 | 15114 |
| snapshot | noretcomp | 0.5 | 752 | 593 | 0 | 0 | 15636 |
| snapshot | noretcomp,cyc=1,... | 0.5 | 1256 | 705 | 0 | 0 | 11370 |

Findings:

- Return compression must be off (`noretcomp`) for Chez: Scheme code enters and leaves
  C without balanced call/ret, so with compression perf's decoder loses its place at C
  `ret`s and half of the fib calls vanish (10909 of 21891), even with few errors.
- On P-cores, `noretcomp` decodes fib exactly in both modes. Dropping MTC (`mtc=0`)
  shrinks the trace by about 20% at no cost. Longer PSB periods
  (`psb_period=5`) make resynchronization after an overflow slower and lose calls.
  Cycle-accurate timing triples the trace and the overflows.
- On E-cores (Skymont), PT overflows 10-30 times more often under the same load, and
  every overflow leaves the decoder's return stack wrong, so mismatches follow. No
  setting tried makes E-core traces usable for exact stacks, not even snapshot mode
  (which avoids AUX loss, not hardware overflow). This probably carries over to other
  machines with weak PT bandwidth, including VMs that pass PT through. Use a P-core.
- The 516 KB mlock limit is per user: two PT recordings at once make the second fail
  with an empty perf.data (silently under `-q`).
- Full traces lose AUX data when perf shares busy cores: with a Racket build running
  on the E-cores, one cp0 trace lost 1016 of 9760 chunks and one list.rkt trace lost
  the whole compile phase. Give perf its own idle core.

## DrRacket startup

DrRacket 9.4.0.3 was installed into the /tmp Racket build (`raco pkg install drracket`)
and started under `xvfb-run` with `bench/drracket-startup.rkt`, which exits once the
first frame is shown. Untraced startup: 4.0 s (3.8-4.1). Under full PT (program on
P-core 2, perf on P-core 0): 8.0 s, 2.4 GB of trace, 1117 of 25018 AUX chunks lost
(4.5%). The magic-trace decode (about 3 hours) is still running; results will be
added here.

## Recommendations

1. Keep the in-process sampler as the default profiler: +2.5% at 999 Hz, whole-run
   coverage, real stacks including continuations, no post-processing.
2. Use PT through magic-trace's Chez mode for questions the sampler cannot answer:
   exact call counts and sequences, per-call and per-GC latency, what happens in one
   slow operation. Run it on a P-core, `noretcomp`, with perf on its own core:
   - snapshot mode (`magic-trace run`, or attach with `-p` and Ctrl-C) for the last
     ~2 ms before an event; decoding takes seconds;
   - full execution only for runs under about a second, and expect ~25 minutes of
     decode per traced second and 3-10% of the trace lost.
3. Ship the jitdump changes (done: `perf-profiling` 840b1a2d). jitdump is cheap enough
   to leave on whenever profiling (10-20 ms at startup, no measurable steady-state
   cost) and also names code for ordinary `perf record`.
4. Worth doing next, in order of value:
   - a trigger that does not need an ELF symbol (e.g. have Chez call a tiny C marker
     function, or use `Magic_trace.take_snapshot`'s protocol), so a Scheme event can
     end a snapshot;
   - magic-trace (or perf) parallel decoding, since decode time is the limit; perf
     can decode separate AUX ranges independently;
   - clear the stack on `exec` (stacks now start with the frames of the `taskset` that
     exec'd the program);
   - port the saved-stack switch to chez_pt_stacks.py, or drop the Python tool in favor
     of magic-trace's Chez mode;
   - root access (or a larger `perf_event_mlock_kb`) would allow 4-64 MB buffers and
     cut AUX loss.

## Files

- Patches: magic-trace diff `pt/magic-trace-chez.diff` (samth/racket-cs-gdb `pt-stacks`),
  Chez jitdump: samth/ChezScheme `perf-profiling`.
- Scripts: `pt-record.sh`, `pt-decode.sh`, `mt-decode.sh`, `mt-chez.sh`, `overhead.sh`,
  `pt-matrix.sh`, `ptstack/chez_pt_stacks.py`, `ptstack/compare_folded.py`, `ptstack/flame.py`.
- Folded stacks per workload and tool: `pt/eval/folded/` (and /tmp/mtrace/eval on huor).
- Raw data on huor: /tmp/mtrace/eval (q/ = quiet recordings, matrix*/ = settings,
  overhead-*/ = overhead runs, sampler-*/ = sampler profiles).
