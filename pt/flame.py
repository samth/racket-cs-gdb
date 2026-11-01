#!/usr/bin/env python3
"""Small flame-graph tools (no external dependencies).

  flame.py collapse < perf-script-output > out.folded
      fold `perf script` callchain output (sample header line, then one
      "\t addr sym+off (dso)" line per frame, leaf first) into folded stacks
  flame.py svg in.folded out.svg [--title T] [--min-frac F]
      render folded stacks (root first, weight last) as an SVG flame graph
"""
import collections, html, re, sys, zlib

FRAME_RE = re.compile(r'^\s+[0-9a-f]+\s+(.*?)(?:\+0x[0-9a-f]+)?\s+\((.*)\)\s*$')


def collapse(inp, out):
    stacks = collections.Counter()
    frames = []
    for line in inp:
        if line.startswith((' ', '\t')) and line.strip():
            m = FRAME_RE.match(line)
            if m:
                name = m.group(1)
                if name == '[unknown]':
                    name = '[unknown ' + m.group(2).rsplit('/', 1)[-1] + ']'
                frames.append(name.replace(';', '_').replace(' ', '_'))
        else:
            if frames:
                stacks[';'.join(reversed(frames))] += 1
            frames = []
    if frames:
        stacks[';'.join(reversed(frames))] += 1
    for k, v in stacks.most_common():
        out.write('%s %d\n' % (k, v))


class Node:
    __slots__ = ('name', 'value', 'children')

    def __init__(self, name):
        self.name, self.value, self.children = name, 0, {}


def color(name):
    h = zlib.crc32(name.encode()) & 0xffff
    if name.startswith('[') or '@' not in name and name[:2] in ('S_', '__', 'mm', 'pt'):
        return 'rgb(%d,%d,%d)' % (200 + h % 50, 120 + h % 60, 60 + h % 40)   # C-ish: orange
    return 'rgb(%d,%d,%d)' % (90 + h % 60, 150 + h % 70, 200 + h % 50)       # Scheme: blue


def svg(path, out_path, title='', min_frac=0.0005, width=1200):
    root = Node('all')
    for line in open(path, errors='replace'):
        st, _, w = line.rstrip('\n').rpartition(' ')
        try:
            w = float(w)
        except ValueError:
            continue
        if w <= 0:
            continue
        node = root
        node.value += w
        for f in st.split(';'):
            node = node.children.setdefault(f, Node(f))
            node.value += w
    total = root.value or 1
    rows = []

    def walk(node, x, depth):
        rows.append((node, x, depth))
        cx = x
        for c in sorted(node.children.values(), key=lambda n: n.name):
            if c.value / total >= min_frac:
                walk(c, cx, depth + 1)
            cx += c.value

    walk(root, 0.0, 0)
    fh = 16
    maxd = max(d for _, _, d in rows)
    height = (maxd + 1) * fh + 50
    o = ['<?xml version="1.0" standalone="no"?>',
         '<svg version="1.1" width="%d" height="%d" xmlns="http://www.w3.org/2000/svg">' % (width, height),
         '<style>text{font-family:monospace;font-size:11px} rect:hover{stroke:black}</style>',
         '<rect width="100%%" height="100%%" fill="#f8f8f8"/>',
         '<text x="10" y="20" font-size="14px">%s</text>' % html.escape(title)]
    scale = (width - 20) / total
    for node, x, d in rows:
        w = node.value * scale
        y = height - (d + 1) * fh - 10
        pct = 100 * node.value / total
        label = '%s (%.2f%%)' % (node.name, pct)
        o.append('<g><title>%s</title><rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" rx="2"/>'
                 % (html.escape(label), 10 + x * scale, y, max(w, 0.1), fh - 1, color(node.name)))
        chars = int(w / 7)
        if chars >= 3:
            text = node.name if len(node.name) <= chars else node.name[:chars - 2] + '..'
            o.append('<text x="%.1f" y="%d">%s</text>' % (13 + x * scale, y + fh - 4, html.escape(text)))
        o.append('</g>')
    o.append('</svg>')
    with open(out_path, 'w') as f:
        f.write('\n'.join(o))


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == 'collapse':
        collapse(sys.stdin, sys.stdout)
    elif len(sys.argv) >= 4 and sys.argv[1] == 'svg':
        args = sys.argv[2:]
        title = ''
        min_frac = 0.0005
        if '--title' in args:
            i = args.index('--title'); title = args[i + 1]; del args[i:i + 2]
        if '--min-frac' in args:
            i = args.index('--min-frac'); min_frac = float(args[i + 1]); del args[i:i + 2]
        svg(args[0], args[1], title, min_frac)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
