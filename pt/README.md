# Intel PT stacks for Chez Scheme and Racket CS

Chez Scheme on x86_64 calls and returns with `jmp`, so Intel Processor Trace
tools that rebuild stacks from `call`/`ret` (perf's `--call-trace`, stock
magic-trace) see every Scheme call as a tail call and show flat stacks. The
tools here reclassify those jumps using the machine code that Chez reports in
a jitdump, and rebuild real Scheme stacks.

Files:

- `chez_pt_stacks.py`: standalone reconstructor. Reads
  `perf script --itrace=b -F time,flags,ip,addr,insnlen,sym,symoff,dso` output
  and the jitdump, writes folded stacks (weighted by trace time in ns), a
  Chrome/Perfetto JSON trace and a summary.
- `chez-pt.sh`: record + `perf inject --jit` + `perf script` + `chez_pt_stacks.py`.
- `magic-trace-chez.diff`: magic-trace patch (against tag v1.2.4) with a Chez mode;
  see below.
- `mt-chez.sh`: runs patched magic-trace in Chez mode with the traced program on one core.
- `pt-record.sh`, `pt-decode.sh`, `mt-decode.sh`: record a full trace (program and perf
  on separate cores) in magic-trace's working-directory layout, then decode it with
  magic-trace's Chez mode and with `chez_pt_stacks.py`.
- `overhead.sh`, `overhead-full.sh`, `pt-matrix.sh`: the overhead and PT-settings
  measurements in EVAL.md.
- `compare_folded.py`: compares two folded profiles (self and inclusive shares per
  procedure, overlap score); used to check PT stacks against the in-process sampler.
- `prune_folded.py`, `flame.py`: shrink folded files; fold perf callchains and render
  a quick SVG flame graph without external tools.
- `eval/`: the workloads, and `eval/folded/` the folded stacks behind EVAL.md
  (workload-tool.folded.gz; tools: magic-trace-chez, chez-pt, perf-itrace, sampler).
- `EVAL.md`: the evaluation (method, numbers, recommendations).
- `REPORT-2026-10-31.md`: earlier notes (frame-chain overhead, first PT experiments).

## What Chez must provide

A Chez (or Racket CS) built from samth/ChezScheme branch `perf-profiling`,
whose jitdump support (`CHEZ_JITDUMP=DIR`) writes, for every code object,
its address, name and bytes, as code is created and as the collector moves it.
For Intel PT the records need TSC timestamps: set `CHEZ_JITDUMP_TSC=1` as well
(`perf record -k mono` cannot be combined with `intel_pt`). The dump is opened
before the boot files load, so boot code is named and decodable too.

## How the reconstruction works

Using the code bytes from the jitdump record named by the `jitted-PID-INDEX.so`
that `perf inject --jit` created:

- non-tail call: a `jmp` preceded within 40 bytes by `lea ret(%rip),%reg` and
  `mov %reg,0x0(%r13)`: push a frame whose return address is `ret` (and whose
  multiple-value return address is the word at `ret-0x20`);
- return: any jump to a pending frame's return or mv-return address pops to that
  frame (covers `jmp *0x0(%r13)`, `jmp *%r12`, `jmp *-0x20(%r12)`, and C `ret`s
  back into Scheme); a `jmp *0x0(%r13)` that matches no frame pops one frame and
  renames the top (resync);
- tail call: any other jump to a code object's entry renames the top frame;
- C: real `call`s push with return address ip+ilen; real `ret`s pop to the matching
  frame, or rename the top when none matches.

magic-trace's Chez mode adds two rules that `chez_pt_stacks.py` lacks:

- it remembers, for each return address, the stack as it was when a frame waiting for
  that address was pushed; an indirect `jmp` or C `ret` to such an address that no
  current frame waits for switches to that stack. Racket's engine swaps and
  continuations do this constantly, and stacks damaged by decode errors recover;
- decode errors keep the stack (stock magic-trace clears it, which truncates every
  later stack); `MAGIC_TRACE_CHEZ_CLEAR_STACK_ON_ERROR=1` restores the old behavior.

Record with `intel_pt/noretcomp/u`: Scheme code enters and leaves C without
balanced call/ret, which breaks perf's return-compression bookkeeping.

## Building magic-trace with the patch

magic-trace master needs OxCaml; tag v1.2.4 builds with stock OCaml 5.2 and
Jane Street v0.17 libraries:

```
export OPAMROOT=/tmp/mtrace/opam OPAMYES=1
opam init -n --bare --disable-sandboxing
opam switch create mt52 ocaml-base-compiler.5.2.1
eval $(opam env --switch=mt52)
git clone https://github.com/janestreet/magic-trace && cd magic-trace
git checkout -b chez v1.2.4
git apply /path/to/magic-trace-chez.diff
opam install . --deps-only
dune build --profile release bin/magic_trace_bin.exe   # dev profile fails on a deprecation alert
```

The diff includes upstream's perf-7 fix (fdf8c2b, "tr strt jmp") without its test
change, which conflicts with v1.2.4.

## Running

```
MAGIC_TRACE_CHEZ=1 MAGIC_TRACE_CHEZ_FOLDED=$PWD/stacks.folded \
CHEZ_JITDUMP=$PWD/jit CHEZ_JITDUMP_TSC=1 \
  magic-trace run -full-execution -timer-resolution Low -snapshot-size 512K \
    -o trace.fxt -- taskset -c 2 scheme -q prog.ss
```

`MAGIC_TRACE_CHEZ=1` adds `noretcomp=1` to the PT event, runs `perf inject --jit`
before decoding, disables the same-symbol-jump dlfilter (it would drop recursive
returns), records `insnlen`, and reclassifies jumps as above.
`MAGIC_TRACE_CHEZ_FOLDED` also writes folded stacks (weighted by trace time; kernel
time appears as `[untraced]`/`[syscall]` leaves). At the end it prints counts of each
classification, the top Scheme entries, the deepest self-recursion per procedure and a
depth histogram.

The patch also fixes three problems in stock magic-trace with perf 7: it cannot parse
`tr end  jmp` lines; the .fxt writer crashes when a stack's reset time runs a few ns
past the event that uses it; and `decode` decodes `perf inject`'s `*.old` file again.

Unprivileged users are limited by `perf_event_mlock_kb` (516 KB by default) to a
512K AUX buffer; see EVAL.md for which PT settings and cores keep data loss low.
