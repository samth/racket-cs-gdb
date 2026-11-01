#!/usr/bin/env python3
"""Rewrite a jitdump's JIT_CODE_MOVE records as JIT_CODE_LOAD records.

Chez writes a JIT_CODE_MOVE record when the collector moves code that already has
a load record. perf 7.0 segfaults on move records in `perf inject --jit`
(jit_repipe_code_move passes a NULL evsel to build_id__mark_dso_hit, which walks
the sample's callchain with it), so use this before `perf inject --jit` with an
affected perf:

    jitdump_expand_moves.py jit-PID.dump        # rewrites the file in place
    jitdump_expand_moves.py IN.dump OUT.dump

Each move becomes a load record at the new address, with the name and code bytes
of the load record it refers to and a fresh code index, so perf (and the PT
decoders) see the same thing as with a jitdump written without move records.
The copied bytes are as they were at the original load, so aligned words that
point into the code itself (such as the mv-return address in a return point's
header) are moved by the same distance as the code, as the collector does.
Expand the dump in place: perf.data names it by its path.

This is enough for naming samples, not for decoding instructions (Intel PT): the
collector also rewrites direct calls and jumps to other code (rel32, or an absolute
address when out of range), which a move record cannot carry. For PT, record with
CHEZ_JITDUMP_NO_MOVES=1 instead.
The output is about as large as such a jitdump.
"""
import mmap
import os
import struct
import sys

LOAD, MOVE = 0, 1


def relocate(code, orig, new):
    """[code] as loaded at [orig], with its aligned self-pointers moved to [new]"""
    delta = new - orig
    if delta == 0:
        return code
    size = len(code)
    b = bytearray(code)
    for o in range((-orig) % 8, size - 7, 8):
        w = struct.unpack_from("<Q", b, o)[0]
        if orig <= w < orig + size:
            struct.pack_into("<Q", b, o, (w + delta) & 0xFFFFFFFFFFFFFFFF)
    return bytes(b)


def expand(src, out):
    with open(src, "rb") as f:
        d = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    hdr_size = struct.unpack_from("<I", d, 8)[0]
    # first pass: where each load record's name+code bytes are, and the largest index
    loads = {}
    pos, top = hdr_size, -1
    while pos + 16 <= len(d):
        rid, size = struct.unpack_from("<II", d, pos)
        if size < 16 or pos + size > len(d):
            break
        if rid == LOAD:
            index = struct.unpack_from("<Q", d, pos + 48)[0]
            addr, csize = struct.unpack_from("<QQ", d, pos + 32)
            loads[index] = (pos + 56, pos + size, addr, csize)
            top = max(top, index)
        pos += size
    end = pos
    nmoves = nmissing = 0
    out.write(d[:hdr_size])
    pos = hdr_size
    while pos < end:
        rid, size = struct.unpack_from("<II", d, pos)
        if rid == MOVE:
            (ts, pid, tid, vma, _old, new, csize,
             index) = struct.unpack_from("<QIIQQQQQ", d, pos + 8)
            body = loads.get(index)
            if body is None:
                nmissing += 1
            else:
                top += 1
                b0, b1, orig, osize = body
                code_start = b1 - osize
                code = relocate(d[code_start:b1], orig, new)
                out.write(struct.pack("<IIQIIQQQQ", LOAD, 56 + (b1 - b0), ts, pid, tid,
                                      vma, new, csize, top))
                out.write(d[b0:code_start])
                out.write(code)
                nmoves += 1
        else:
            out.write(d[pos:pos + size])
        pos += size
    return len(loads), nmoves, nmissing


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) == 3 else src + ".tmp"
    with open(dst, "wb") as out:
        nloads, nmoves, nmissing = expand(src, out)
    if len(sys.argv) == 2:
        os.replace(dst, src)
    print(f"{nloads} loads, {nmoves} moves rewritten as loads"
          + (f", {nmissing} moves of unknown code dropped" if nmissing else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
