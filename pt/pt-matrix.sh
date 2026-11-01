#!/bin/bash
# PT settings matrix on fib(20): data size, overflow/mismatch/lost counts, per core.
# usage: pt-matrix.sh OUTDIR
out=$1; mkdir -p $out
B=/tmp/mtrace/prof-tsc/ta6le/boot/ta6le
S=/tmp/mtrace/bin/scheme-tsc-v2
cfgs=("tsc" "noretcomp" "noretcomp,mtc=0" "noretcomp,mtc=0,psb_period=5" "noretcomp,mtc=0,psb_period=5,branch=1" "noretcomp,cyc=1,cyc_thresh=1,mtc_period=0")
[ -f $out/matrix.tsv ] || printf "core\tmode\tconfig\tperf.data MB\toverflow\tmismatch\tlost\tother errors\tAUX lost\tfib entries\n" > $out/matrix.tsv
for core in ${CORES:-2 12}; do
  for mode in full snap; do
    for cfg in "${cfgs[@]}"; do
      tag=$(echo "c$core-$mode-$cfg" | tr ',=' '_-')
      d=$out/$tag; mkdir -p $d/jit
      snap=(); [ $mode = snap ] && snap=(--snapshot=e)
      CHEZ_JITDUMP=$d/jit CHEZ_JITDUMP_TSC=1 taskset -c $core perf record -q "${snap[@]}" -m 8,512K -e intel_pt/$cfg/u -o $d/pt.data -- \
        $S -b $B/petite.boot -b $B/scheme.boot -q /tmp/mtrace/pt/fib20.ss > /dev/null 2> $d/record.err < /dev/null
      taskset -c 8-19 perf inject --jit -i $d/pt.data -o $d/pt.jit.data 2> $d/inject.err
      read ov mm lost tot fib < <(taskset -c 8-19 perf script -i $d/pt.jit.data --itrace=be --ns -F time,flags,ip,addr,sym,symoff,dso 2> $d/script.err | \
        awk '/Overflow packet/{ov++} /doesn.t match/{mm++} /Lost trace data/{lost++} /instruction trace error/{tot++} /jmp.*=> *[0-9a-f]+ fib@fib20.ss#0\+0x0 /{fib++} END{print ov+0, mm+0, lost+0, tot+0, fib+0}')
      oth=$(( tot - ov - mm - lost ))
      aux=$(grep -o "AUX data lost [0-9]* times out of [0-9]*" $d/script.err | head -1 | sed 's/AUX data lost //;s/ times out of /\//')
      mb=$(du -m $d/pt.data | cut -f1)
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" $core $mode "$cfg" $mb $ov $mm $lost $oth "${aux:-0}" $fib >> $out/matrix.tsv
    done
  done
done
cat $out/matrix.tsv
