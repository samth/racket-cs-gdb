#!/bin/bash
# usage: chez-pt.sh OUTDIR [--chrome-start NAME] -- scheme args...
# Records an Intel PT trace of a Chez Scheme run (needs the CHEZ_JITDUMP_TSC patch),
# injects the jitdump, and rebuilds Scheme stacks: OUTDIR/stacks.folded, OUTDIR/trace.json,
# OUTDIR/summary.txt. Run it pinned (taskset -c N) to keep overflow packets down.
set -e
out=$1; shift
extra=()
while [ "$1" != -- ]; do extra+=("$1"); shift; done
shift
mkdir -p "$out/jit"
CHEZ_JITDUMP="$out/jit" CHEZ_JITDUMP_TSC=1 \
  perf record -m 8,512K -e intel_pt/noretcomp/u -o "$out/pt.data" -- "$@" < /dev/null
perf inject --jit -i "$out/pt.data" -o "$out/pt.jit.data"
perf script -i "$out/pt.jit.data" --itrace=b --ns -F time,flags,ip,addr,insnlen,sym,symoff,dso 2> "$out/script.err" \
  | python3 -I "$(dirname "$0")/chez_pt_stacks.py" - --jitdump "$out"/jit/jit-*.dump \
      --folded "$out/stacks.folded" --chrome "$out/trace.json" "${extra[@]}" > "$out/summary.txt"
cat "$out/summary.txt"
