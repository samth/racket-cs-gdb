#!/usr/bin/env python3
"""Rebuild Chez Scheme call stacks from an Intel PT branch trace.

Chez on x86_64 calls and returns with jmp, so perf's call/return flags see no
Scheme calls. This script reclassifies the branches using the code bytes from a
Chez jitdump (CHEZ_JITDUMP=dir, TSC timestamps):

  non-tail call  jmp preceded by `lea ret(%rip),%reg; mov %reg,0(%r13)`:
                 push a frame whose return address is ret
  return         `jmp *0x0(%r13)` or another jmp to a pending return address,
                 or a multiple-value return (`jmp *-0x20(%r12)`) to the mv-return
                 address stored 0x20 bytes before a return point: pop to that
                 frame; a frame-return jmp with no such frame resyncs by popping one
  tail call      other jmp to a code-object entry: replace the top frame
  C call/ret     real call/ret instructions, matched by return address

Input is the output of
  perf script -i X.jit.data --itrace=b --ns -F time,flags,ip,addr,insnlen,sym,symoff,dso
Outputs folded stacks weighted by trace time (ns), a Chrome/Perfetto JSON
trace, and a summary with depth histograms.
"""
import argparse, collections, json, os, re, struct, sys

LINE_RE = re.compile(
    r'^\s*(\d+)\.(\d+):\s+(.*?)\s+([0-9a-f]+) (.*?) \((.*?)\) =>\s+([0-9a-f]+) (.*?) \((.*?)\)(?: ilen: (\d+))?\s*$')
JIT_DSO_RE = re.compile(r'jitted-\d+-(\d+)\.so$')


def read_jitdump(path):
    """code_index -> (code_addr, code bytes, name)"""
    code = {}
    with open(path, 'rb') as f:
        data = f.read()
    magic, version, hdr_size = struct.unpack_from('<III', data, 0)
    assert magic == 0x4A695444, 'not a jitdump file'
    pos = hdr_size
    while pos + 16 <= len(data):
        rid, size, _ts = struct.unpack_from('<IIQ', data, pos)
        if size < 16:
            break
        if rid == 0:  # JIT_CODE_LOAD
            _pid, _tid, _vma, addr, csize, idx = struct.unpack_from('<IIQQQQ', data, pos + 16)
            npos = pos + 56
            nend = data.index(b'\0', npos)
            name = data[npos:nend].decode('utf-8', 'replace')
            code[idx] = (addr, data[nend + 1:nend + 1 + csize], name)
        pos += size
    return code


def split_sym(s):
    if '+0x' in s:
        name, off = s.rsplit('+0x', 1)
        return name, int(off, 16)
    return s, None


class Frame:
    __slots__ = ('name', 'ret', 'mvret', 'kind')

    def __init__(self, name, ret, kind, mvret=None):
        self.name, self.ret, self.kind, self.mvret = name, ret, kind, mvret


def is_frame_return(b, o, n):
    # jmp *0x0(%r13): 41 ff 65 00
    return n == 4 and b[o:o + 4] == b'\x41\xff\x65\x00'


def call_return_address(b, o, entry):
    """If the jmp at offset o follows `lea ret(%rip),%reg; mov %reg,0(%r13)`, return
    (ret, mvret) as absolute addresses; mvret is the word in the return point's header."""
    lo = max(0, o - 40)
    for p in range(o - 7, lo - 1, -1):
        rex, op, modrm = b[p], b[p + 1], b[p + 2]
        if rex in (0x48, 0x4c) and op == 0x8d and (modrm & 0xc7) == 0x05:
            disp = struct.unpack_from('<i', b, p + 3)[0]
            target = p + 7 + disp
            if target <= o:
                continue
            reg = ((modrm >> 3) & 7) | (8 if rex == 0x4c else 0)
            # look for mov %reg,0x0(%r13) between the lea and the jmp
            for q in range(p + 7, o - 3):
                mrex, mop, mmod = b[q], b[q + 1], b[q + 2]
                if mrex in (0x49, 0x4d) and mop == 0x89 and (mmod & 0xc7) == 0x45 and b[q + 3] == 0:
                    sreg = ((mmod >> 3) & 7) | (8 if mrex == 0x4d else 0)
                    if sreg == reg:
                        mv = struct.unpack_from('<Q', b, target - 0x20)[0] if target >= 0x20 else None
                        return entry + target, mv
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('branches', help='perf script --itrace=b output (or - for stdin)')
    ap.add_argument('--jitdump', required=True)
    ap.add_argument('--folded', help='write folded stacks (ns weights)')
    ap.add_argument('--chrome', help='write Chrome/Perfetto JSON trace')
    ap.add_argument('--chrome-max-events', type=int, default=2_000_000)
    ap.add_argument('--chrome-start', help='start the Chrome trace when this procedure is first entered')
    ap.add_argument('--root', default='<root>')
    ap.add_argument('--log-depth', type=int, help='when depth first exceeds this, print the preceding classified events')
    args = ap.parse_args()

    code = read_jitdump(args.jitdump)
    stack = [Frame(args.root, None, 'root')]
    stats = collections.Counter()
    depth_hist = collections.Counter()      # per event landing in Scheme code
    entries = collections.Counter()         # non-tail + tail calls by callee
    unmatched_c = collections.Counter()     # (from, to) of C returns with no matching frame
    max_depth = 0
    folded = collections.Counter()
    last_t = None
    chrome = []
    chrome_full = False
    chrome_on = not args.chrome_start

    def emit(ph, name, t):
        nonlocal chrome_full
        if args.chrome and chrome_on and not chrome_full:
            chrome.append({'ph': ph, 'name': name, 'ts': t / 1000.0, 'pid': 1, 'tid': 1})
            if len(chrome) >= args.chrome_max_events:
                chrome_full = True

    def push(name, ret, kind, t, mvret=None):
        nonlocal chrome_on
        if not chrome_on and name == args.chrome_start:
            chrome_on = True
            for f in stack:
                emit('B', f.name, t)
        stack.append(Frame(name, ret, kind, mvret))
        emit('B', name, t)

    def pop(t):
        f = stack.pop()
        emit('E', f.name, t)

    def replace_top(name, t):
        if len(stack) == 1:
            push(name, None, 'unknown', t)
        elif stack[-1].name != name:
            f = stack[-1]
            emit('E', f.name, t)
            f.name = name
            emit('B', name, t)

    def pop_to(ret, t):
        for i in range(len(stack) - 1, 0, -1):
            if stack[i].ret == ret or stack[i].mvret == ret:
                while len(stack) > i:
                    pop(t)
                return True
        return False

    ring = collections.deque(maxlen=400)
    logged = False
    inp = sys.stdin if args.branches == '-' else open(args.branches, errors='replace')
    for line in inp:
        m = LINE_RE.match(line)
        if not m:
            if 'instruction trace error' in line:
                stats['trace errors'] += 1
            continue
        t = int(m.group(1)) * 1_000_000_000 + int(m.group(2))
        flags = m.group(3).strip()
        src = int(m.group(4), 16)
        ssym, soff = split_sym(m.group(5))
        sdso = m.group(6)
        dst = int(m.group(7), 16)
        dsym, doff = split_sym(m.group(8))
        ddso = m.group(9)
        ilen = int(m.group(10)) if m.group(10) else 0

        if last_t is not None and args.folded:
            folded[';'.join(f.name for f in stack)] += t - last_t
        last_t = t
        if flags == 'jcc':
            continue
        stats['branches ' + flags] += 1
        if flags.startswith('tr'):
            continue
        sj = JIT_DSO_RE.search(sdso)
        dj = JIT_DSO_RE.search(ddso)

        if flags == 'call':
            push(dsym, src + ilen, 'c', t)
            stats['C calls'] += 1
        elif flags == 'return':
            if pop_to(dst, t):
                stats['C returns matched'] += 1
            else:
                stats['C returns unmatched'] += 1
                unmatched_c[(ssym, dsym)] += 1
                replace_top(dsym, t)
        elif flags == 'jmp':
            if pop_to(dst, t):
                # any jump to a pending return address is a return
                stats['returns matched' if sj and is_frame_return(code[int(sj.group(1))][1], soff, ilen)
                      else 'returns matched (other jmp)'] += 1
            elif sj and soff is not None and int(sj.group(1)) in code:
                entry, b, _ = code[int(sj.group(1))]
                if is_frame_return(b, soff, ilen):
                    stats['returns unmatched (resync)'] += 1
                    if len(stack) > 1:
                        pop(t)
                    replace_top(dsym, t)
                else:
                    ret = call_return_address(b, soff, entry)
                    if ret is not None:
                        push(dsym, ret[0], 's', t, ret[1])
                        stats['non-tail calls'] += 1
                        entries[dsym] += 1
                    elif doff == 0 and dj:
                        replace_top(dsym, t)
                        stats['tail calls'] += 1
                        entries[dsym] += 1
                    elif dj and sj.group(1) == dj.group(1):
                        stats['local jmps'] += 1
                    else:
                        replace_top(dsym, t)
                        stats['other transfers'] += 1
            else:
                if doff == 0 and dj:
                    replace_top(dsym, t)
                    stats['C->Scheme entry jmps'] += 1
                    entries[dsym] += 1
                elif dj or ssym != dsym:
                    replace_top(dsym, t)
                    stats['other transfers'] += 1
        else:
            stats['unhandled ' + flags] += 1

        if args.log_depth:
            ring.append('%d %s %s' % (len(stack) - 1, flags, line.strip()[:220]))
            if not logged and len(stack) - 1 > args.log_depth:
                logged = True
                print('\n'.join(ring), file=sys.stderr)
                print('STACK: ' + ';'.join(f.name + ('@%x' % f.ret if f.ret else '') for f in stack), file=sys.stderr)
        if dj:
            d = len(stack) - 1
            depth_hist[d] += 1
            max_depth = max(max_depth, d)

    if args.folded:
        with open(args.folded, 'w') as f:
            for k, v in folded.most_common():
                if v > 0:
                    f.write('%s %d\n' % (k, v))
    if args.chrome:
        t = last_t or 0
        while len(stack) > 1 and not chrome_full:
            pop(t)
        with open(args.chrome, 'w') as f:
            json.dump({'traceEvents': chrome, 'displayTimeUnit': 'ns'}, f)

    print('max Scheme-event depth', max_depth)
    for k, v in sorted(stats.items()):
        print('%-32s %d' % (k, v))
    print('top entries:', ', '.join('%s=%d' % kv for kv in entries.most_common(8)))
    print('top unmatched C returns:', ', '.join('%s->%s=%d' % (a, b, n) for (a, b), n in unmatched_c.most_common(8)))
    print('depth histogram (events landing in Scheme code):')
    for d in sorted(depth_hist):
        print('  %3d %d' % (d, depth_hist[d]))


if __name__ == '__main__':
    main()
