#!/bin/bash
# usage: pt-decode.sh OUTDIR EXECUTABLE
# Decodes a pt-record.sh recording twice: patched magic-trace in Chez mode
# (OUTDIR/trace.fxt, OUTDIR/mt.folded, OUTDIR/mt.log) and chez_pt_stacks.py on the
# same injected data (OUTDIR/py.folded, OUTDIR/py.summary.txt). Times both.
out=$(cd "$1" && pwd); exe=$2
cd "$out"
s=$(date +%s.%N)
MAGIC_TRACE_CHEZ=1 MAGIC_TRACE_CHEZ_FOLDED="$out/mt.folded" \
  taskset -c 8-19 /tmp/mtrace/bin/magic-trace-v4 decode -executable "$exe" \
    -working-directory "$out/wd" -o "$out/trace.fxt" > "$out/mt.log" 2>&1
e=$(date +%s.%N)
echo "magic-trace decode $(echo "$e - $s" | bc) s" > "$out/decode-times.txt"
grep "Warning: perf reported" mt.log | sed 's/.*trace: //; s/ @ IP.*//' | sort | uniq -c | sort -rn > errors.txt
s=$(date +%s.%N)
taskset -c 8-19 perf script -i wd/perf.data.chez-jit --itrace=be --ns -F time,flags,ip,addr,insnlen,sym,symoff,dso 2> py.script.err \
  | taskset -c 8-19 python3 -I /tmp/mtrace/ptstack/chez_pt_stacks.py - --jitdump jit/jit-*.dump \
      --folded py.folded --durations S_do_gc,S_gc_oce,compile-file > py.summary.txt
e=$(date +%s.%N)
echo "python decode $(echo "$e - $s" | bc) s" >> "$out/decode-times.txt"
