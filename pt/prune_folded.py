#!/usr/bin/env python3
"""prune_folded.py IN OUT [--keep FRACTION] [--drop-over FRACTION]

Aggregates duplicate stacks and keeps the heaviest stacks that together cover
FRACTION (default 0.995) of the total weight; prints what it kept. --drop-over
first drops single stacks heavier than that fraction of the total (PT timestamp
gaps)."""
import collections, sys


def main():
    args = sys.argv[1:]
    keep = 0.995
    drop_over = None
    if '--drop-over' in args:
        i = args.index('--drop-over'); drop_over = float(args[i + 1]); del args[i:i + 2]
    if '--keep' in args:
        i = args.index('--keep'); keep = float(args[i + 1]); del args[i:i + 2]
    src, dst = args
    stacks = collections.Counter()
    for line in open(src, errors='replace'):
        st, _, w = line.rstrip('\n').rpartition(' ')
        try:
            stacks[st] += int(float(w))
        except ValueError:
            pass
    total = sum(stacks.values()) or 1
    if drop_over is not None:
        big = [st for st, w in stacks.items() if w > drop_over * total]
        for st in big:
            print('%s: dropped a stack with %.1f%% of the weight (timestamp gap): ...%s'
                  % (src, 100.0 * stacks[st] / total, st[-120:]))
            del stacks[st]
        total = sum(stacks.values()) or 1
    acc = 0
    n = 0
    with open(dst, 'w') as f:
        for st, w in stacks.most_common():
            if acc >= keep * total:
                break
            f.write('%s %d\n' % (st, w))
            acc += w
            n += 1
    print('%s: kept %d of %d stacks, %.2f%% of weight' % (src, n, len(stacks), 100.0 * acc / total))


if __name__ == '__main__':
    main()
