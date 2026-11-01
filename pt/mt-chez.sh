#!/bin/bash
# usage: mt-chez.sh OUTDIR CORE [magic-trace run flags...] -- program args...
# Runs patched magic-trace in Chez mode. The traced program runs on CORE;
# magic-trace, perf and decoding run on CPUs 8-19. Keeps perf.data in OUTDIR/wd
# (for `magic-trace decode -working-directory OUTDIR/wd`), writes OUTDIR/trace.fxt,
# OUTDIR/stacks.folded, OUTDIR/run.log, OUTDIR/errors.txt (decode error counts).
out=$1; core=$2; shift 2
flags=()
while [ "$1" != -- ]; do flags+=("$1"); shift; done
shift
mkdir -p "$out/jit"
out=$(cd "$out" && pwd)
start=$(date +%s.%N)
MAGIC_TRACE_CHEZ=1 MAGIC_TRACE_CHEZ_FOLDED="$out/stacks.folded" \
CHEZ_JITDUMP="$out/jit" CHEZ_JITDUMP_TSC=1 \
  taskset -c 8-19 /tmp/mtrace/bin/magic-trace run -working-directory "$out/wd" \
    "${flags[@]}" -o "$out/trace.fxt" -- taskset -c "$core" "$@" > "$out/run.log" 2>&1 < /dev/null
rc=$?
end=$(date +%s.%N)
echo "rc=$rc wall(record+decode)=$(echo "$end - $start" | bc) s" >> "$out/run.log"
grep "Warning: perf reported" "$out/run.log" | sed 's/.*trace: //; s/ @ IP.*//' | sort | uniq -c | sort -rn > "$out/errors.txt"
grep -v "Warning: perf reported" "$out/run.log" | grep -E "chez mode|AUX|rc=|Captured|instruction trace errors"
cat "$out/errors.txt"
