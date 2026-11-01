# Folded stacks behind EVAL.md

Names are `WORKLOAD-TOOL.folded.gz`. Workloads: `fib20`, `cp0` (Chez compiling
s/cp0.ss), `rkt-load` (`racket -l racket -e 1`), `rkt-list`
(`managed-compile-zo` of racket/list.rkt), `drracket` (DrRacket startup under xvfb). Tools:

- `magic-trace-chez`: patched magic-trace, Chez mode v6 (`MAGIC_TRACE_CHEZ_FOLDED`);
  weights are ns of trace time, including kernel time as `[untraced]`/`[syscall]`
  leaves.
- `chez-pt`: `chez_pt_stacks.py` on the same recording; weights in ns.
- `perf-itrace`: `perf script --itrace=i100usg` callchains on the same recording,
  folded by `flame.py collapse`; one count per 100 µs of trace.
- `sampler`: Chez's in-process sampler at 10 kHz (`CHEZ_SAMPLE_MARK=0`), 10-20 runs
  pooled; one count per sample.

PT files were pruned with `prune_folded.py` to fit: each keeps its heaviest stacks
covering 99.5% of the weight, except `rkt-list-magic-trace-chez` (98%) and
`cp0-magic-trace-chez` (95%). Single stacks holding more than 5% of the weight because
of timestamp jumps in perf's decode were dropped from `rkt-load-*` (one 22 s jump) and
`cp0-magic-trace-chez` (three stacks, 38% in all). `drracket-magic-trace-chez` is magic-trace v7 (it covers all threads); the five
heaviest stacks (glib and PulseAudio threads blocked in syscalls) and every stack
with a kernel leaf were dropped, then the heaviest stacks covering 92% of the rest
were kept. Sampler files are complete.
