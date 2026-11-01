#!/bin/bash
# usage: pt-record.sh OUTDIR CORE -- program args...
# Full-execution Intel PT recording in magic-trace's working-directory layout, so that
# `magic-trace decode -working-directory OUTDIR/wd` can decode it later. The program
# runs on CORE, perf on CPU ${PERFCPU:-0}; jitdump goes to OUTDIR/jit.
out=$1; core=$2; shift 3
mkdir -p "$out/jit" "$out/wd"
out=$(cd "$out" && pwd)
echo "()" > "$out/wd/hits.sexp"
echo "((callgraph_mode()))" > "$out/wd/recording_data.sexp"
start=$(date +%s.%N)
CHEZ_JITDUMP="$out/jit" CHEZ_JITDUMP_TSC=1 \
  taskset -c ${PERFCPU:-0} perf record -o "$out/wd/perf.data" --timestamp \
    -e intel_pt/${PTCFG:-noretcomp=1}/u -m 8,512K -- taskset -c "$core" "$@" \
    > "$out/record.out" 2> "$out/record.err" < /dev/null
end=$(date +%s.%N)
echo "record wall $(echo "$end - $start" | bc) s; perf.data $(stat -c %s "$out/wd/perf.data") bytes" | tee "$out/record.txt"
grep -E "AUX|lost|Captured" "$out/record.err" | tee -a "$out/record.txt"
