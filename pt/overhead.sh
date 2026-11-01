#!/bin/bash
# Tracing overhead on the Chez compiler bench (cp0, cpprim, cpnanopass at o3),
# rounds alternating over configs. Traced program on CORE; perf/magic-trace on 8-19.
# usage: overhead.sh OUTDIR CORE ROUNDS
out=$1; core=$2; rounds=${3:-5}
mkdir -p $out
B=/tmp/mtrace/prof-tsc/ta6le/boot/ta6le
S=/tmp/mtrace/bin/scheme-tsc-v2
prog=($S -b $B/petite.boot -b $B/scheme.boot -q --script /tmp/mtrace/bench/compile-timed.ss
      /tmp/mtrace/prof-tsc/ta6le/s /tmp/mtrace/scratch/ov /tmp/mtrace/bench/src-prof cp0.ss cpprim.ss cpnanopass.ss)
configs="none jitdump sampler pt-snap pt-snap-cyc pt-full mt-snap"
printf "round\tconfig\telapsed_ms\tcpu_ms\twall_total_s\n" > $out/raw.tsv
for r in $(seq 1 $rounds); do
  for c in $configs; do
    j=$out/jit-$c; mkdir -p $j
    jd=(CHEZ_JITDUMP=$j CHEZ_JITDUMP_TSC=1)
    t0=$(date +%s.%N)
    case $c in
      none) env taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      jitdump) env "${jd[@]}" taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      sampler) env CHEZ_SAMPLE_STACKS=$out/sampler.folded CHEZ_SAMPLE_MARK=0 taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      pt-snap) env "${jd[@]}" taskset -c 8-19 perf record -q --snapshot=e -m 8,512K -e intel_pt/noretcomp/u -o $out/snap.data -- taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      pt-snap-cyc) env "${jd[@]}" taskset -c 8-19 perf record -q --snapshot=e -m 8,512K -e intel_pt/noretcomp,cyc=1,cyc_thresh=1,mtc_period=0/u -o $out/snapcyc.data -- taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      pt-full) env "${jd[@]}" taskset -c 8-19 perf record -q -m 8,512K -e intel_pt/noretcomp/u -o $out/full.data -- taskset -c $core "${prog[@]}" 2> $out/err.txt > /dev/null ;;
      mt-snap) env "${jd[@]}" MAGIC_TRACE_CHEZ=1 taskset -c 8-19 /tmp/mtrace/bin/magic-trace-v4 run -snapshot-size 512K -o $out/mt.fxt -- taskset -c $core "${prog[@]}" > $out/mt.log 2> $out/err.txt ;;
    esac
    t1=$(date +%s.%N)
    line=$(grep -h elapsed-ms $out/err.txt $out/mt.log 2>/dev/null | tail -1)
    printf "%s\t%s\t%s\t%s\t%s\n" $r $c $(echo $line | awk '{print $2}') $(echo $line | awk '{print $4}') $(echo "$t1 - $t0" | bc) >> $out/raw.tsv
  done
done
ls -la $out/*.data $out/mt.fxt >> $out/sizes.txt 2>&1
cat $out/raw.tsv
