#!/usr/bin/env python3
"""Compare two folded-stack profiles (e.g. a PT reconstruction and the in-process sampler).

Weights are normalized to fractions of each profile's total. For every procedure name
it reports self share (leaf) and inclusive share (on the stack at all), then the top
names by either and an overlap score: sum over names of min(share_a, share_b), which is
1.0 for identical distributions.

Names are normalized: a leading "[C]" is dropped, "_[i]"/"_[j]" marks are dropped,
and with --scheme-only, C frames (those not present as Scheme names) are folded into
their nearest Scheme caller so that both sides see only Scheme procedures.
"""
import argparse, collections, re, sys

MARK_RE = re.compile(r'_\[[ij]\]$')


def norm(name):
    if name.startswith('[C]'):
        name = name[3:]
    return MARK_RE.sub('', name)


def load(path, keep=None, drop_prefix=0):
    stacks = collections.Counter()
    for line in open(path, errors='replace'):
        line = line.rstrip('\n')
        if not line:
            continue
        st, _, w = line.rpartition(' ')
        try:
            w = float(w)
        except ValueError:
            continue
        frames = [norm(f) for f in st.split(';')][drop_prefix:]
        if keep is not None:
            frames = [f for f in frames if keep(f)]
        if frames:
            stacks[tuple(frames)] += w
    return stacks


def shares(stacks):
    total = sum(stacks.values()) or 1.0
    self_ = collections.Counter()
    incl = collections.Counter()
    for frames, w in stacks.items():
        self_[frames[-1]] += w / total
        for f in set(frames):
            incl[f] += w / total
    return self_, incl, total


def overlap(a, b):
    return sum(min(a[k], b[k]) for k in set(a) | set(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--names', default='A,B')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--scheme-only', action='store_true',
                    help='keep only frames whose names appear in B (the sampler) as Scheme frames')
    args = ap.parse_args()
    na, nb = args.names.split(',')
    keep = None
    if args.scheme_only:
        scheme = set()
        for line in open(args.b, errors='replace'):
            st = line.rpartition(' ')[0]
            for f in st.split(';'):
                if not f.startswith('[C]'):
                    scheme.add(norm(f))
        keep = lambda f: f in scheme
    A = load(args.a, keep)
    B = load(args.b, keep)
    sa, ia, ta = shares(A)
    sb, ib, tb = shares(B)
    print(f'{na}: total weight {ta:.0f}, {len(A)} distinct stacks; {nb}: total weight {tb:.0f}, {len(B)} distinct stacks')
    print(f'self-share overlap {overlap(sa, sb):.3f}; inclusive-share overlap (normalized) '
          f'{overlap(ia, ib) / max(sum(ia.values()), sum(ib.values())):.3f}')
    for title, x, y in (('self', sa, sb), ('inclusive', ia, ib)):
        print(f'\ntop {args.top} by {title} share ({na} / {nb}):')
        names = sorted(set(x) | set(y), key=lambda k: -(x[k] + y[k]))[:args.top]
        for k in names:
            print(f'  {100 * x[k]:6.2f}%  {100 * y[k]:6.2f}%  {k}')


if __name__ == '__main__':
    main()
