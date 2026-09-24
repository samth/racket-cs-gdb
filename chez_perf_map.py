#!/usr/bin/env python3
"""Write a perf symbol map for a running Racket CS process.

  chez_perf_map.py PID [--equates PATH] [--output PATH] [--unique]

perf cannot name the machine code that Chez Scheme generates, since it
has no ELF symbols. For such code, `perf report` reads /tmp/perf-PID.map,
which lists "START SIZE NAME" for each piece of code. This script finds
every Chez code object in the process and writes that file, so samples in
Racket code show up under their Chez names (such as `remainder` or
`count-primes`) instead of as raw addresses.

Typical use:

  perf record -F 999 -g -p PID -- sleep 10
  chez_perf_map.py PID
  perf report

Write the map while the process is still running, after recording: the
collector can move code compiled after startup, and the map describes
where the code is when it is written. The Racket core's code never moves.

Reading another process's memory needs the same permission as attaching a
debugger; see allow-ptrace. With --unique, each name gets its code
object's address, so that different code objects with the same name
(such as `proc` or `#f`) stay apart in perf's report.

The layout of Chez objects comes from the build's equates.h, looked for at
racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h relative to the
process's executable, or given with --equates; otherwise built-in values
for Racket CS 9.3.0.8 are used.
"""

import argparse
import os
import re
import sys

# Checked against racket/src/build/cs/c/ChezScheme/boot/ta6le/equates.h
# for Racket CS 9.3.0.8.
DEFAULT_LAYOUT = {
    "type_code": 0xBE,
    "code_type_disp": 0x1,
    "code_length_disp": 0x9,
    "code_name_disp": 0x19,
    "code_data_disp": 0x41,
    "code_flags_offset": 0x8,
    "type_typed_object": 0x7,
    "type_string": 0x2,
    "string_type_disp": 0x1,
    "string_data_disp": 0x9,
    "string_length_offset": 0x4,
    "string_char_bytes": 0x4,
    "char_data_offset": 0x8,
    "type_char": 0x16,
    "sfalse": 0x6,
}


def read_equates(path, names=DEFAULT_LAYOUT):
    """The constants in names, read from an equates.h."""
    values = dict(names)
    found = set()
    pattern = re.compile(r"#define\s+(\w+)\s+(?:\(ptr\))?(-?0x[0-9A-Fa-f]+|-?\d+)\s*$")
    with open(path) as f:
        for line in f:
            m = pattern.match(line)
            if m and m.group(1) in values:
                values[m.group(1)] = int(m.group(2), 0)
                found.add(m.group(1))
    missing = set(names) - found
    if missing:
        raise ValueError("%s lacks %s" % (path, ", ".join(sorted(missing))))
    return values


def equates_for_executable(exe):
    """equates.h in the build tree around a racket executable, or None."""
    bindir = os.path.dirname(os.path.realpath(exe))
    path = os.path.join(bindir, "..", "src", "build", "cs", "c", "ChezScheme",
                        "boot", "ta6le", "equates.h")
    return path if os.path.exists(path) else None


class Layout:
    def __init__(self, values):
        self.__dict__.update(values)


def scheme_string(read, s, L, max_len=1000):
    """The Chez string at tagged pointer s, "#f" for #f, or None if s is not
    a well-formed string."""
    if s == L.sfalse:
        return "#f"
    if s & 7 != L.type_typed_object:
        return None
    header = read(s + L.string_type_disp, 8)
    if header is None:
        return None
    tw = int.from_bytes(header, "little")
    if tw & 7 != L.type_string:
        return None
    n = tw >> L.string_length_offset
    if n > max_len:
        return None
    data = read(s + L.string_data_disp, n * L.string_char_bytes)
    if data is None:
        return None
    chars = []
    for i in range(n):
        c = int.from_bytes(data[4 * i:4 * i + 4], "little")
        if c & 0xFF != L.type_char:
            return None
        chars.append(chr(c >> L.char_data_offset))
    return "".join(chars)


def find_code_objects(read, mappings, L, chunk=16 << 20):
    """Yield (start, size, name) for each Chez code object in mappings, a
    list of (lo, hi) address ranges; read(addr, n) returns bytes or None.

    A code object's type word has the code type in its low byte and is
    word-aligned. Candidates must also have a plausible length that fits in
    the mapping and a name that is #f or a well-formed string. After a code
    object is found, the scan continues past its machine code, which cannot
    contain another object."""
    type_byte = bytes([L.type_code])
    for lo, hi in mappings:
        a = lo
        while a < hi:
            end = min(a + chunk, hi)
            data = read(a, end - a)
            if data is None:
                a = end
                continue
            i = data.find(type_byte)
            next_a = end
            while i != -1:
                addr = a + i
                if addr % 8 == 0 and i + 16 <= len(data):
                    tw = int.from_bytes(data[i:i + 8], "little")
                    n = int.from_bytes(data[i + 8:i + 16], "little")
                    p = addr - L.code_type_disp
                    start = p + L.code_data_disp
                    if (tw >> L.code_flags_offset) < (1 << 16) and 0 < n < (1 << 24) \
                       and start + n <= hi:
                        name_ptr = read(p + L.code_name_disp, 8)
                        name = name_ptr and scheme_string(read, int.from_bytes(name_ptr, "little"), L)
                        if name is not None:
                            yield start, n, name
                            skip = start + n
                            if skip >= end:
                                next_a = skip
                                break
                            i = data.find(type_byte, skip - a)
                            continue
                i = data.find(type_byte, i + 1)
            a = next_a


def write_perf_map(path, entries, unique=False):
    """Write entries, (start, size, name) triples, in perf's map format."""
    with open(path, "w") as f:
        for start, size, name in sorted(entries):
            if unique:
                name = "%s [%#x]" % (name, start)
            f.write("%x %x %s\n" % (start, size, name.replace("\n", " ")))


# ----------------------------------------------------------------------
# Reading another process through /proc

def proc_mappings(pid):
    """(lo, hi) of the process's readable anonymous mappings and heap,
    where Chez allocates."""
    maps = []
    with open("/proc/%d/maps" % pid) as f:
        for line in f:
            parts = line.split()
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            name = parts[5] if len(parts) >= 6 else ""
            if "r" in parts[1] and name in ("", "[heap]"):
                maps.append((lo, hi))
    return maps


def proc_reader(pid):
    mem = open("/proc/%d/mem" % pid, "rb", buffering=0)

    def read(addr, n):
        try:
            mem.seek(addr)
            data = mem.read(n)
        except (OSError, OverflowError, ValueError):
            return None
        return data if data is not None and len(data) == n else None

    return read


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Write /tmp/perf-PID.map naming the Chez code in a Racket CS process.")
    ap.add_argument("pid", type=int)
    ap.add_argument("--equates", help="Chez's ta6le equates.h for this Racket build")
    ap.add_argument("--output", help="map file to write (default /tmp/perf-PID.map)")
    ap.add_argument("--unique", action="store_true",
                    help="add each code object's address to its name")
    args = ap.parse_args(argv)

    equates = args.equates or equates_for_executable("/proc/%d/exe" % args.pid)
    L = Layout(read_equates(equates) if equates else DEFAULT_LAYOUT)
    try:
        read = proc_reader(args.pid)
        entries = list(find_code_objects(read, proc_mappings(args.pid), L))
    except PermissionError as e:
        sys.exit("cannot read process %d's memory (%s); see allow-ptrace" % (args.pid, e))
    out = args.output or "/tmp/perf-%d.map" % args.pid
    write_perf_map(out, entries, args.unique)
    print("wrote %d code objects to %s (layout from %s)"
          % (len(entries), out, equates or "built-in values for Racket CS 9.3.0.8"))


if __name__ == "__main__":
    main()
