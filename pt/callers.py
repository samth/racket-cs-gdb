#!/usr/bin/env python3
"""callers.py FOLDED NAME [NAME...] [--depth N] [--top K]

For each NAME, the share of total (user-leaf) weight where NAME is the leaf, and the
heaviest caller chains (the N frames above it), as a share of NAME's own weight."""
import collections, sys

KERNELISH = ('[untraced]', '[syscall]', '[unknown')


def main():
    args = sys.argv[1:]
    depth, top = 3, 8
    if '--depth' in args:
        i = args.index('--depth'); depth = int(args[i + 1]); del args[i:i + 2]
    if '--top' in args:
        i = args.index('--top'); top = int(args[i + 1]); del args[i:i + 2]
    path, names = args[0], set(args[1:])
    total = 0
    own = collections.Counter()
    chains = collections.defaultdict(collections.Counter)
    for line in open(path, errors='replace'):
        st, _, w = line.rstrip('\n').rpartition(' ')
        try:
            w = int(float(w))
        except ValueError:
            continue
        leaf_start = st.rfind(';') + 1
        leaf = st[leaf_start:]
        if leaf.startswith(KERNELISH):
            continue
        total += w
        if leaf in names:
            own[leaf] += w
            frames = st.split(';')
            chains[leaf][' < '.join(reversed(frames[-1 - depth:-1]))] += w
    for n in sorted(names, key=lambda n: -own[n]):
        print('%s: %.2f%% of user time' % (n, 100.0 * own[n] / max(total, 1)))
        for c, w in chains[n].most_common(top):
            print('   %5.1f%%  < %s' % (100.0 * w / max(own[n], 1), c))


if __name__ == '__main__':
    main()
