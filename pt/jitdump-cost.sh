#!/bin/bash
# Untraced cost of CHEZ_JITDUMP in Racket CS: rkt-list compile and DrRacket startup,
# configs off / jitdump / jitdump+TSC, alternating rounds, program on P-core 2.
# usage: jitdump-cost.sh OUTDIR ROUNDS
out=$1; rounds=${2:-10}
mkdir -p $out
R=/tmp/mtrace/racket/racket/bin/racket
jd=/tmp/mtrace/scratch/jdcost; mkdir -p $jd
printf "round\tworkload\tconfig\twall_ms\tcycles\tinstructions\ttask_clock_ms\tdump_bytes\trecords\tdistinct_code\n" > $out/raw.tsv
for r in $(seq 1 $rounds); do
  for w in rkt-list drracket; do
    for c in off jitdump jitdump-tsc; do
      envs=(PLTADDONDIR=/tmp/mtrace/addon PLTUSERHOME=/tmp/mtrace/scratch/drhome)
      [ $c = jitdump ] && envs+=(CHEZ_JITDUMP=$jd)
      [ $c = jitdump-tsc ] && envs+=(CHEZ_JITDUMP=$jd CHEZ_JITDUMP_TSC=1)
      stat=$out/stat.tmp
      if [ $w = rkt-list ]; then
        d=/tmp/mtrace/scratch/jdcost-list/$r-$c; mkdir -p $d
        cp /tmp/mtrace/racket/racket/collects/racket/list.rkt $d/
        s=$(date +%s%N)
        (cd $d && env "${envs[@]}" perf stat -x, -e cpu_core/cycles/u,cpu_core/instructions/u,task-clock -o $stat \
           taskset -c 2 $R -l racket/base -l compiler/cm -e '(managed-compile-zo "list.rkt")')
        e=$(date +%s%N); wall=$(( (e - s) / 1000000 ))
      else
        wall=$(env "${envs[@]}" timeout 300 xvfb-run -a perf stat -x, -e cpu_core/cycles/u,cpu_core/instructions/u,task-clock -o $stat \
           taskset -c 2 $R /tmp/mtrace/bench/drracket-startup.rkt 2>&1 | grep -o 'drracket-startup-ms [0-9.]*' | awk '{print $2}')
      fi
      cyc=$(grep cycles $stat | cut -d, -f1); ins=$(grep instructions $stat | cut -d, -f1); tc=$(grep task-clock $stat | cut -d, -f1)
      bytes=0; recs=0; distinct=0
      for f in $jd/jit-*.dump; do
        [ -f "$f" ] || continue
        bytes=$(stat -c %s "$f")
        read recs distinct < <(python3 -I -c '
import struct, sys
d = open(sys.argv[1], "rb").read()
pos = struct.unpack_from("<I", d, 8)[0]; n = 0; seen = set()
while pos + 16 <= len(d):
    rid, size = struct.unpack_from("<II", d, pos)
    if size < 16: break
    if rid == 0:
        csize = struct.unpack_from("<Q", d, pos + 40)[0]
        end = d.index(b"\0", pos + 56)
        seen.add((d[pos + 56:end], csize)); n += 1
    pos += size
print(n, len(seen))' "$f")
        rm -f -- "$f"
      done
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" $r $w $c "$wall" "$cyc" "$ins" "$tc" $bytes $recs $distinct >> $out/raw.tsv
    done
  done
done
cat $out/raw.tsv
