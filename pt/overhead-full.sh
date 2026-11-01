#!/bin/bash
# Full-trace overhead on the compiler bench with perf on its own P-core (0), program on P-core 1.
out=/tmp/mtrace/eval/overhead-full-p0; mkdir -p $out /tmp/mtrace/scratch/ovf/jit
B=/tmp/mtrace/prof-tsc/ta6le/boot/ta6le
S=/tmp/mtrace/bin/scheme-tsc-v2
prog=($S -b $B/petite.boot -b $B/scheme.boot -q --script /tmp/mtrace/bench/compile-timed.ss
      /tmp/mtrace/prof-tsc/ta6le/s /tmp/mtrace/scratch/ovf /tmp/mtrace/bench/src-prof cp0.ss cpprim.ss cpnanopass.ss)
for r in 1 2 3; do
  echo "$r none $(taskset -c 1 "${prog[@]}" 2>&1 >/dev/null | grep elapsed)"
  CHEZ_JITDUMP=/tmp/mtrace/scratch/ovf/jit CHEZ_JITDUMP_TSC=1 taskset -c 0 perf record -m 8,512K \
    -e intel_pt/noretcomp/u -o $out/full.data -- taskset -c 1 "${prog[@]}" > /dev/null 2> $out/err.txt
  echo "$r pt-full-perf-P0 $(grep -h elapsed $out/err.txt) $(grep -h Captured $out/err.txt)"
done
