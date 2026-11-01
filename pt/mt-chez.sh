#!/bin/bash
# usage: mt-chez.sh OUTDIR CORE [magic-trace run flags...] -- program args...
# Runs patched magic-trace in Chez mode. The traced program runs on CORE;
# magic-trace, perf and decoding run on CPUs 8-19. Keeps perf.data in OUTDIR/wd
# (for `magic-trace decode -working-directory OUTDIR/wd`), writes OUTDIR/trace.fxt,
# OUTDIR/stacks.folded, OUTDIR/run.log, OUTDIR/errors.txt (decode error counts).
# Sets CHEZ_JITDUMP_NO_MOVES=1 unless it is set (CHEZ_JITDUMP_NO_MOVES= keeps move records):
# decoding needs moved code's bytes as they are after the move (direct calls and jumps are
# rel32), and perf 7.0's `perf inject --jit`, which magic-trace runs, crashes on move records.
# Snapshots on demand: pass `-trigger .` and run the racket or scheme executable
# itself; the program calls magic_trace_stop_indicator (Chez perf-profiling 9310551,
# racket-perf/magic-trace.rkt), CHEZ_MAGIC_TRACE_GC_MS=N snapshots after a GC of N ms,
# and CHEZ_JITDUMP_LAZY=1 writes the jitdump only at the snapshot.
out=$1; core=$2; shift 2
flags=()
while [ "$1" != -- ]; do flags+=("$1"); shift; done
shift
mkdir -p "$out/jit"
out=$(cd "$out" && pwd)
start=$(date +%s.%N)
export MAGIC_TRACE_CHEZ=1 MAGIC_TRACE_CHEZ_FOLDED="$out/stacks.folded" \
  CHEZ_JITDUMP="$out/jit" CHEZ_JITDUMP_TSC=1 CHEZ_JITDUMP_NO_MOVES=${CHEZ_JITDUMP_NO_MOVES-1}
mt=${MT:-/tmp/mtrace/bin/magic-trace-v8}
if [[ " ${flags[*]} " == *" -trigger "* ]]; then
  # magic-trace looks the trigger symbol up in the executable it runs, so run the
  # program directly, with magic-trace and perf pinned to CORE as well
  taskset -c "$core" $mt run -working-directory "$out/wd" \
    "${flags[@]}" -o "$out/trace.fxt" -- "$@" > "$out/run.log" 2>&1 < /dev/null
else
  taskset -c ${PERFCPUS:-8-19} $mt run -working-directory "$out/wd" \
    "${flags[@]}" -o "$out/trace.fxt" -- taskset -c "$core" "$@" > "$out/run.log" 2>&1 < /dev/null
fi
rc=$?
end=$(date +%s.%N)
echo "rc=$rc wall(record+decode)=$(echo "$end - $start" | bc) s" >> "$out/run.log"
grep "Warning: perf reported" "$out/run.log" | sed 's/.*trace: //; s/ @ IP.*//' | sort | uniq -c | sort -rn > "$out/errors.txt"
grep -v "Warning: perf reported" "$out/run.log" | grep -E "chez mode|AUX|rc=|Captured|instruction trace errors"
cat "$out/errors.txt"
