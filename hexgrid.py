#!/usr/bin/env python3
"""hexgrid: offline hex-grid toolkit.

Converts between offset, axial and cube coordinates, measures distances,
walks rings and ranges, draws hex lines, and reads plain-text terrain maps
to answer movement-cost, path, line-of-sight and picture questions.
Standard library only; no network, no files written unless asked.
"""

import argparse
import heapq
import html
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

LAYOUTS = ("odd-r", "even-r", "odd-q", "even-q")
AXIAL_DIRECTIONS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))
_NUDGE = Fraction(1, 1_000_000)
_SQRT3 = math.sqrt(3)
_TOKEN = re.compile(r"\S+")
_FILLS = ("#c7e9c0", "#fdd49e", "#bcbddc", "#9ecae1", "#fee391", "#d9d9d9", "#fbb4b9", "#a1d99b")
_BLOCKED_FILL = "#4d4d4d"
_INF = math.inf


# ---------------------------------------------------------------- coordinates

def _check_layout(layout):
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; use one of {', '.join(LAYOUTS)}")


def offset_to_axial(col, row, layout):
    """Convert an offset (col, row) to axial (q, r) for the given layout."""
    _check_layout(layout)
    if layout == "odd-r":
        return (col - (row - (row & 1)) // 2, row)
    if layout == "even-r":
        return (col - (row + (row & 1)) // 2, row)
    if layout == "odd-q":
        return (col, row - (col - (col & 1)) // 2)
    return (col, row - (col + (col & 1)) // 2)


def axial_to_offset(q, r, layout):
    """Convert axial (q, r) back to offset (col, row) for the given layout."""
    _check_layout(layout)
    if layout == "odd-r":
        return (q + (r - (r & 1)) // 2, r)
    if layout == "even-r":
        return (q + (r + (r & 1)) // 2, r)
    if layout == "odd-q":
        return (q, r + (q - (q & 1)) // 2)
    return (q, r + (q + (q & 1)) // 2)


def to_cube(h):
    """Axial (q, r) to cube (x, y, z) with x + y + z == 0."""
    q, r = h
    return (q, -q - r, r)


def distance(a, b):
    """Number of hex steps between two axial cells."""
    dq = a[0] - b[0]
    dr = a[1] - b[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def neighbors(h):
    """The six adjacent cells, in AXIAL_DIRECTIONS order."""
    return [(h[0] + dq, h[1] + dr) for dq, dr in AXIAL_DIRECTIONS]


def ring(center, radius):
    """Every cell exactly `radius` steps from center, walked around the ring."""
    if radius < 0:
        raise ValueError("radius must be zero or more")
    if radius == 0:
        return [center]
    dq, dr = AXIAL_DIRECTIONS[4]
    q, r = center[0] + dq * radius, center[1] + dr * radius
    out = []
    for dq, dr in AXIAL_DIRECTIONS:
        for _ in range(radius):
            out.append((q, r))
            q += dq
            r += dr
    return out


def hex_range(center, radius):
    """Every cell within `radius` steps of center, centre first, row by row."""
    if radius < 0:
        raise ValueError("radius must be zero or more")
    cq, cr = center
    out = []
    for dq in range(-radius, radius + 1):
        for dr in range(max(-radius, -dq - radius), min(radius, -dq + radius) + 1):
            out.append((cq + dq, cr + dr))
    return out


def _round_axial(q, r):
    """Round fractional axial coordinates to the nearest whole hex."""
    x, z = q, r
    y = -x - z
    rx, ry, rz = round(x), round(y), round(z)
    dx, dy, dz = abs(rx - x), abs(ry - y), abs(rz - z)
    if dx > dy and dx > dz:
        rx = -ry - rz
    elif dy > dz:
        ry = -rx - rz
    else:
        rz = -rx - ry
    return (rx, rz)


def hex_line(a, b):
    """Cells crossed by a straight line from a to b (both ends included).

    A tiny nudge keeps lines that pass exactly between two cells from
    flipping between sides, so the result is deterministic.
    """
    n = distance(a, b)
    if n == 0:
        return [a]
    aq, ar = a[0] + _NUDGE, a[1] + _NUDGE
    bq, br = b[0] + _NUDGE, b[1] + _NUDGE
    out = []
    for i in range(n + 1):
        t = Fraction(i, n)
        out.append(_round_axial(aq + (bq - aq) * t, ar + (br - ar) * t))
    return out


# ---------------------------------------------------------------- maps

@dataclass(frozen=True)
class Terrain:
    char: str
    cost: int | None  # movement points to enter; None means impassable
    name: str
    opaque: bool = False


@dataclass
class HexMap:
    layout: str
    terrains: dict
    rows: list
    cells: dict = field(init=False, repr=False)

    def __post_init__(self):
        self.cells = {
            offset_to_axial(col, row_i, self.layout): ch
            for row_i, row in enumerate(self.rows)
            for col, ch in enumerate(row)
        }

    def terrain_at(self, h):
        ch = self.cells.get(h)
        return None if ch is None else self.terrains[ch]

    def cost_into(self, h):
        t = self.terrain_at(h)
        return None if t is None else t.cost


_DIRECTIVE_LAYOUT_HINT = ", ".join(LAYOUTS)


def parse_map(text):
    """Parse a hexgrid map. Returns (HexMap or None, sorted error list).

    Each error is (line, column, message), 1-based. A map is only returned
    when there are no errors.
    """
    text = text.lstrip("﻿")
    errors = []
    layout = None
    terrain_defs = {}
    opaque_refs = []
    grid_rows = []
    grid_line = None
    in_grid = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        if in_grid:
            if raw.strip() == "":
                continue
            grid_rows.append((lineno, raw))
            continue
        tokens = [(m.group(), m.start() + 1) for m in _TOKEN.finditer(raw)]
        if not tokens or tokens[0][0].startswith("#"):
            continue
        key, key_col = tokens[0]
        args = tokens[1:]
        if key == "grid":
            in_grid = True
            grid_line = lineno
            if args:
                errors.append((lineno, args[0][1], "grid takes no arguments"))
        elif key == "layout":
            where = args[0][1] if args else key_col
            if len(args) != 1 or args[0][0] not in LAYOUTS:
                errors.append((lineno, where, f"layout must be one of {_DIRECTIVE_LAYOUT_HINT}"))
            elif layout is not None:
                errors.append((lineno, key_col, "duplicate layout directive"))
            else:
                layout = args[0][0]
        elif key == "terrain":
            if len(args) < 3:
                errors.append((lineno, key_col, "terrain needs: terrain <char> <cost or -> <name>"))
                continue
            (char, char_col), (cost_text, cost_col) = args[0], args[1]
            name = " ".join(tok for tok, _ in args[2:])
            if len(char) != 1:
                errors.append((lineno, char_col, "terrain character must be exactly one character"))
            elif char in terrain_defs:
                errors.append((lineno, char_col, f"duplicate terrain '{char}'"))
            elif cost_text == "-":
                terrain_defs[char] = Terrain(char, None, name)
            elif cost_text.isascii() and cost_text.isdigit() and int(cost_text) > 0:
                terrain_defs[char] = Terrain(char, int(cost_text), name)
            else:
                errors.append((lineno, cost_col, "cost must be a positive whole number, or '-' for impassable"))
        elif key == "opaque":
            if len(args) != 1 or len(args[0][0]) != 1:
                errors.append((lineno, key_col, "opaque takes exactly one terrain character"))
            else:
                opaque_refs.append((lineno, args[0][1], args[0][0]))
        else:
            errors.append((lineno, key_col, f"unknown directive '{key}'"))

    if layout is None:
        errors.append((1, 1, "missing layout directive"))

    opaque_chars = set()
    for lineno, col, ch in opaque_refs:
        if ch in terrain_defs:
            opaque_chars.add(ch)
        else:
            errors.append((lineno, col, f"opaque refers to unknown terrain '{ch}'"))
    terrains = {ch: replace(t, opaque=ch in opaque_chars) for ch, t in terrain_defs.items()}

    if grid_line is None:
        errors.append((1, 1, "missing grid section"))
    rows = []
    width = None
    for lineno, raw in grid_rows:
        space_at = next((i for i, c in enumerate(raw) if c.isspace()), None)
        if space_at is not None:
            errors.append((lineno, space_at + 1, "grid rows may not contain spaces"))
            continue
        for i, ch in enumerate(raw):
            if ch not in terrain_defs:
                errors.append((lineno, i + 1, f"unknown terrain '{ch}' in grid"))
        if width is None:
            width = len(raw)
        elif len(raw) != width:
            errors.append((lineno, len(raw) + 1, f"row has {len(raw)} hexes, expected {width}"))
        rows.append(raw)
    if grid_line is not None and not grid_rows:
        errors.append((grid_line, 1, "grid has no rows"))

    errors.sort(key=lambda e: (e[0], e[1]))
    if errors or layout is None:
        return None, errors
    return HexMap(layout, terrains, rows), errors


# ---------------------------------------------------------------- pathing

def reachable(m, start, budget):
    """Map each cell reachable from start within budget movement points to its cost.

    Entering a cell costs that terrain's cost; the start cell itself is free.
    """
    best = {start: 0}
    heap = [(0, start)]
    while heap:
        cost, h = heapq.heappop(heap)
        if cost > best[h]:
            continue
        for n in neighbors(h):
            step = m.cost_into(n)
            if step is None or cost + step > budget:
                continue
            if cost + step < best.get(n, _INF):
                best[n] = cost + step
                heapq.heappush(heap, (cost + step, n))
    return best


def shortest_path(m, a, b):
    """Cheapest route from a to b as (total_cost, [cells]), or None if unreachable."""
    best = {a: 0}
    prev = {}
    heap = [(0, a)]
    while heap:
        cost, h = heapq.heappop(heap)
        if h == b:
            break
        if cost > best[h]:
            continue
        for n in neighbors(h):
            step = m.cost_into(n)
            if step is None:
                continue
            if cost + step < best.get(n, _INF):
                best[n] = cost + step
                prev[n] = h
                heapq.heappush(heap, (cost + step, n))
    if b not in best:
        return None
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    path.reverse()
    return best[b], path


def line_of_sight(m, a, b):
    """Return (visible, blocker). Opaque terrain and off-map cells block sight."""
    for h in hex_line(a, b)[1:-1]:
        t = m.terrain_at(h)
        if t is None or t.opaque:
            return False, h
    return True, None


# ---------------------------------------------------------------- rendering

def ascii_map(m, path=(), start=None, goal=None):
    """Schematic text picture of the map. Route cells are '*', start 'S', goal 'G'."""
    marks = {h: "*" for h in path}
    if start is not None:
        marks[start] = "S"
    if goal is not None:
        marks[goal] = "G"
    flat = m.layout.endswith("-q")
    placed = {}
    for row_i, row in enumerate(m.rows):
        for col_i, ch in enumerate(row):
            h = offset_to_axial(col_i, row_i, m.layout)
            if flat:
                shift = (col_i & 1) if m.layout == "odd-q" else 1 - (col_i & 1)
                pos = (3 * col_i, 2 * row_i + shift)
            else:
                shift = (row_i & 1) if m.layout == "odd-r" else 1 - (row_i & 1)
                pos = (4 * col_i + 2 * shift, row_i)
            placed[pos] = marks.get(h, ch)
    width = max(x for x, _ in placed) + 1
    height = max(y for _, y in placed) + 1
    canvas = [[" "] * width for _ in range(height)]
    for (x, y), ch in placed.items():
        canvas[y][x] = ch
    return "\n".join("".join(line).rstrip() for line in canvas)


def _centre(h, pointy, size):
    q, r = h
    if pointy:
        return (size * _SQRT3 * (q + r / 2), size * 1.5 * r)
    return (size * 1.5 * q, size * _SQRT3 * (r + q / 2))


def svg_map(m, path=(), size=24):
    """Standalone SVG picture of the map, with route cells outlined in red."""
    route = set(path)
    fills = {}
    passable_index = 0
    for ch, t in m.terrains.items():
        if t.cost is None:
            fills[ch] = _BLOCKED_FILL
        else:
            fills[ch] = _FILLS[passable_index % len(_FILLS)]
            passable_index += 1
    pointy = m.layout.endswith("-r")
    base = 30 if pointy else 0
    angles = [math.radians(base + 60 * k) for k in range(6)]
    shapes = []
    xs, ys = [], []
    for h in sorted(m.cells):
        ch = m.cells[h]
        t = m.terrains[ch]
        cx, cy = _centre(h, pointy, size)
        corners = [(cx + size * math.cos(a), cy + size * math.sin(a)) for a in angles]
        xs.extend(x for x, _ in corners)
        ys.extend(y for _, y in corners)
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in corners)
        outline = 'stroke="#d62728" stroke-width="3"' if h in route else 'stroke="#333333" stroke-width="1"'
        col, row = axial_to_offset(h[0], h[1], m.layout)
        cost_text = "impassable" if t.cost is None else f"cost {t.cost}"
        title = html.escape(f"{col},{row} {t.name} ({cost_text})")
        shapes.append(
            f'  <polygon points="{points}" fill="{fills[ch]}" {outline}>'
            f"<title>{title}</title></polygon>"
        )
    left = min(xs) - size
    top = min(ys) - size
    width = max(xs) - min(xs) + 2 * size
    height = max(ys) - min(ys) + 2 * size
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left:.2f} {top:.2f} {width:.2f} {height:.2f}">',
            *shapes,
            "</svg>",
        ]
    )


# ---------------------------------------------------------------- command line

def _parse_offset(text):
    m = re.fullmatch(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*", text)
    if not m:
        raise ValueError(f"coordinate {text!r} must look like COL,ROW")
    return int(m.group(1)), int(m.group(2))


def _plain_hex(text, layout):
    col, row = _parse_offset(text)
    return offset_to_axial(col, row, layout)


def _map_hex(m, text):
    col, row = _parse_offset(text)
    h = offset_to_axial(col, row, m.layout)
    if h not in m.cells:
        raise ValueError(f"{text} is outside the map")
    return h


def _offset_text(h, layout):
    col, row = axial_to_offset(h[0], h[1], layout)
    return f"{col},{row}"


def _require_map(path):
    text = Path(path).read_text(encoding="utf-8")
    m, errors = parse_map(text)
    for line, col, msg in errors:
        print(f"{path}:{line}:{col}: error: {msg}", file=sys.stderr)
    return None if errors else m


def _cmd_convert(args):
    col, row = _parse_offset(args.coord)
    h = offset_to_axial(col, row, args.layout)
    x, y, z = to_cube(h)
    print(f"offset  {col},{row} ({args.layout})")
    print(f"axial   {h[0]},{h[1]}")
    print(f"cube    {x},{y},{z}")
    return 0


def _cmd_distance(args):
    a = _plain_hex(args.a, args.layout)
    b = _plain_hex(args.b, args.layout)
    print(distance(a, b))
    return 0


def _cmd_neighbors(args):
    h = _plain_hex(args.coord, args.layout)
    for n in neighbors(h):
        print(_offset_text(n, args.layout))
    return 0


def _cmd_ring(args):
    h = _plain_hex(args.coord, args.layout)
    cells = ring(h, args.radius)
    for c in cells:
        print(_offset_text(c, args.layout))
    print(f"{len(cells)} hexes at distance {args.radius}")
    return 0


def _cmd_range(args):
    h = _plain_hex(args.coord, args.layout)
    cells = hex_range(h, args.radius)
    for c in cells:
        print(_offset_text(c, args.layout))
    print(f"{len(cells)} hexes within {args.radius}")
    return 0


def _cmd_line(args):
    a = _plain_hex(args.a, args.layout)
    b = _plain_hex(args.b, args.layout)
    cells = hex_line(a, b)
    print(" -> ".join(_offset_text(c, args.layout) for c in cells))
    print(f"{len(cells) - 1} steps")
    return 0


def _cmd_lint(args):
    text = Path(args.map).read_text(encoding="utf-8")
    m, errors = parse_map(text)
    if errors:
        for line, col, msg in errors:
            print(f"{args.map}:{line}:{col}: error: {msg}")
        print(f"{len(errors)} error(s)")
        return 1
    print(f"ok: {args.map} ({len(m.cells)} hexes, layout {m.layout})")
    return 0


def _cmd_info(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    counts = Counter(ch for row in m.rows for ch in row)
    passable = sum(n for ch, n in counts.items() if m.terrains[ch].cost is not None)
    width = max(len(row) for row in m.rows)
    print(f"layout    {m.layout}")
    print(f"size      {width} x {len(m.rows)} ({len(m.cells)} hexes)")
    print(f"passable  {passable}")
    print("terrain")
    for ch, t in m.terrains.items():
        cost = "-" if t.cost is None else str(t.cost)
        flags = " opaque" if t.opaque else ""
        print(f"  {ch}  cost {cost:>2}  {counts.get(ch, 0):>4} hexes  {t.name}{flags}")
    return 0


def _cmd_path(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    a = _map_hex(m, args.start)
    b = _map_hex(m, args.goal)
    result = shortest_path(m, a, b)
    if result is None:
        print(f"no path from {args.start} to {args.goal}")
        return 1
    total, cells = result
    print(f"total cost {total} over {len(cells) - 1} steps")
    running = 0
    for i, h in enumerate(cells):
        step = 0 if i == 0 else m.cost_into(h)
        running += step
        ch = m.cells[h]
        print(f"{i:>3}  {_offset_text(h, m.layout):>7}  {ch}  +{step}  = {running}")
    return 0


def _cmd_reach(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    if args.budget < 0:
        raise ValueError("budget must be zero or more")
    start = _map_hex(m, args.start)
    best = reachable(m, start, args.budget)
    items = sorted(best.items(), key=lambda kv: (kv[1], axial_to_offset(kv[0][0], kv[0][1], m.layout)))
    for h, cost in items:
        print(f"{_offset_text(h, m.layout):>7}  cost {cost}")
    print(f"{len(items)} hexes reachable within {args.budget} points")
    return 0


def _cmd_los(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    a = _map_hex(m, args.a)
    b = _map_hex(m, args.b)
    visible, blocker = line_of_sight(m, a, b)
    if visible:
        print("clear line of sight")
        return 0
    t = m.terrain_at(blocker)
    what = "off the map" if t is None else t.name
    print(f"blocked at {_offset_text(blocker, m.layout)} ({what})")
    return 1


def _cmd_show(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    start = goal = None
    route = []
    if args.path:
        start = _map_hex(m, args.path[0])
        goal = _map_hex(m, args.path[1])
        result = shortest_path(m, start, goal)
        if result is None:
            print(f"no path from {args.path[0]} to {args.path[1]}", file=sys.stderr)
            return 1
        route = result[1]
    print(ascii_map(m, route, start, goal))
    if args.path:
        print("legend: S start, G goal, * route")
    return 0


def _cmd_svg(args):
    m = _require_map(args.map)
    if m is None:
        return 1
    route = []
    if args.path:
        start = _map_hex(m, args.path[0])
        goal = _map_hex(m, args.path[1])
        result = shortest_path(m, start, goal)
        if result is None:
            print(f"no path from {args.path[0]} to {args.path[1]}", file=sys.stderr)
            return 1
        route = result[1]
    text = svg_map(m, route) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="hexgrid",
        description="Offline hex-grid toolkit: coordinates, rings, lines, terrain maps, paths and pictures.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_layout(sp):
        sp.add_argument("--layout", choices=LAYOUTS, default="odd-r",
                        help="offset layout for plain coordinates (default: odd-r)")

    sp = sub.add_parser("convert", help="show a COL,ROW coordinate as offset, axial and cube")
    sp.add_argument("coord", metavar="COL,ROW")
    add_layout(sp)
    sp.set_defaults(func=_cmd_convert)

    sp = sub.add_parser("distance", help="hex steps between two coordinates")
    sp.add_argument("a", metavar="FROM")
    sp.add_argument("b", metavar="TO")
    add_layout(sp)
    sp.set_defaults(func=_cmd_distance)

    sp = sub.add_parser("neighbors", help="the six neighbours of a coordinate")
    sp.add_argument("coord", metavar="COL,ROW")
    add_layout(sp)
    sp.set_defaults(func=_cmd_neighbors)

    sp = sub.add_parser("ring", help="every hex exactly RADIUS steps away")
    sp.add_argument("coord", metavar="COL,ROW")
    sp.add_argument("radius", type=int)
    add_layout(sp)
    sp.set_defaults(func=_cmd_ring)

    sp = sub.add_parser("range", help="every hex within RADIUS steps")
    sp.add_argument("coord", metavar="COL,ROW")
    sp.add_argument("radius", type=int)
    add_layout(sp)
    sp.set_defaults(func=_cmd_range)

    sp = sub.add_parser("line", help="hexes crossed by a straight line")
    sp.add_argument("a", metavar="FROM")
    sp.add_argument("b", metavar="TO")
    add_layout(sp)
    sp.set_defaults(func=_cmd_line)

    sp = sub.add_parser("lint", help="check a map file, with file:line:col errors")
    sp.add_argument("map")
    sp.set_defaults(func=_cmd_lint)

    sp = sub.add_parser("info", help="summarise a map: size, terrain counts, costs")
    sp.add_argument("map")
    sp.set_defaults(func=_cmd_info)

    sp = sub.add_parser("path", help="cheapest route between two map cells")
    sp.add_argument("map")
    sp.add_argument("start", metavar="FROM")
    sp.add_argument("goal", metavar="TO")
    sp.set_defaults(func=_cmd_path)

    sp = sub.add_parser("reach", help="cells reachable within a movement budget")
    sp.add_argument("map")
    sp.add_argument("start", metavar="FROM")
    sp.add_argument("budget", type=int)
    sp.set_defaults(func=_cmd_reach)

    sp = sub.add_parser("los", help="line of sight between two map cells")
    sp.add_argument("map")
    sp.add_argument("a", metavar="FROM")
    sp.add_argument("b", metavar="TO")
    sp.set_defaults(func=_cmd_los)

    sp = sub.add_parser("show", help="ASCII picture of a map, optionally with a route")
    sp.add_argument("map")
    sp.add_argument("--path", nargs=2, metavar=("FROM", "TO"))
    sp.set_defaults(func=_cmd_show)

    sp = sub.add_parser("svg", help="SVG picture of a map, optionally with a route")
    sp.add_argument("map")
    sp.add_argument("--path", nargs=2, metavar=("FROM", "TO"))
    sp.add_argument("-o", "--output", help="write to this file instead of stdout")
    sp.set_defaults(func=_cmd_svg)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
