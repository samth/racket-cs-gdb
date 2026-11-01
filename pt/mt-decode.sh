#!/bin/bash
# usage: mt-decode.sh OUTDIR EXECUTABLE TAG
# Re-decode a pt-record.sh recording with /tmp/mtrace/bin/magic-trace-TAG in Chez
# mode: OUTDIR/mt-TAG.folded, OUTDIR/mt-TAG.log, OUTDIR/trace-TAG.fxt.
cd "$1" || exit 1
s=$(date +%s.%N)
MAGIC_TRACE_CHEZ=1 MAGIC_TRACE_CHEZ_FOLDED=$PWD/mt-$3.folded \
  taskset -c 8-19 /tmp/mtrace/bin/magic-trace-$3 decode -executable "$2" \
    -working-directory wd -o trace-$3.fxt > mt-$3.log 2>&1
e=$(date +%s.%N)
echo "magic-trace $3 decode $(echo "$e - $s" | bc) s" >> decode-times.txt
