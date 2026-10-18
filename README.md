# hexgrid

**Offline hex-grid toolkit for tabletop maps, wargames and hex-based games.**

Hexgrid converts coordinates between offset, axial and cube forms, measures distances, walks rings and ranges, draws hex lines, and reads a plain-text terrain map to answer movement, path and line-of-sight questions. It also draws the map as ASCII or SVG. It is one Python file, uses only the standard library, and makes no network calls.

## Features

- **Coordinate conversion**: convert between offset (`odd-r`, `even-r`, `odd-q`, `even-q`), axial and cube coordinates, with exact round-trips.
- **Distance**: exact hex-step count between any two cells.
- **Neighbours**: list the six cells that touch any coordinate.
- **Rings**: every cell exactly N steps away, walked in order around the ring.
- **Ranges**: every cell within N steps, with the count checked against the hexagonal formula.
- **Hex lines**: the cells a straight line crosses, with a deterministic tie-break for lines that pass between cells.
- **Terrain maps**: a plain-text format with a terrain legend, a layout directive and an optional opaque (sight-blocking) flag.
- **Lint with exact errors**: every problem is reported as `file:line:col: error: message`, and `lint` exits 1 on any error, so it works as a CI gate.
- **Map summary**: `info` gives size, passable hex count, and a tally of each terrain with its cost.
- **Cheapest path**: Dijkstra over movement costs, printed step by step with running totals.
- **Movement reach**: every cell reachable within a movement budget, with the cost to reach each.
- **Line of sight**: checks the hex line between two cells for opaque terrain or off-map cells, and names the blocker.
- **ASCII picture**: a schematic text view of the map with start, goal and route marked.
- **SVG export**: a standalone SVG with terrain fills, hover titles and a highlighted route, written to stdout or a file.

## Quick start

The repository includes a small map at `examples/valley.hex`:

```text
# A small valley: open ground, forest, hills, a lake and rock.
layout odd-r
terrain . 1 open ground
terrain f 2 forest
terrain m 3 hill
terrain ~ - lake
terrain # - rock
opaque f
opaque #
grid
........
..ff~~..
.fff~mm.
..ff..m.
...#....
```

Check it, summarise it, and find a route across it:

```sh
python3 hexgrid.py lint examples/valley.hex
# ok: examples/valley.hex (40 hexes, layout odd-r)

python3 hexgrid.py info examples/valley.hex
python3 hexgrid.py path examples/valley.hex 0,0 7,4
python3 hexgrid.py show examples/valley.hex --path 0,0 7,4
python3 hexgrid.py los examples/valley.hex 0,2 7,2
```

Coordinates are written as `COL,ROW` in the map's offset layout, the same numbers you would count along the rows and columns of the file.

Convert a bare coordinate, with no map needed:

```sh
python3 hexgrid.py convert 1,1 --layout odd-r
# offset  1,1 (odd-r)
# axial   1,1
# cube    1,-2,1
```

## How to run

Requires Python 3.10 or newer. No install step.

```text
python3 hexgrid.py convert COL,ROW [--layout L]
python3 hexgrid.py distance FROM TO [--layout L]
python3 hexgrid.py neighbors COL,ROW [--layout L]
python3 hexgrid.py ring COL,ROW RADIUS [--layout L]
python3 hexgrid.py range COL,ROW RADIUS [--layout L]
python3 hexgrid.py line FROM TO [--layout L]
python3 hexgrid.py lint MAP
python3 hexgrid.py info MAP
python3 hexgrid.py path MAP FROM TO
python3 hexgrid.py reach MAP FROM BUDGET
python3 hexgrid.py los MAP FROM TO
python3 hexgrid.py show MAP [--path FROM TO]
python3 hexgrid.py svg MAP [--path FROM TO] [-o FILE]
```

Coordinates may be negative on plain (no-map) commands. Put `--` before the arguments so argparse does not read them as flags, e.g. `python3 hexgrid.py distance -- -1,0 2,3`.

Exit codes: `0` success, `1` a map has errors, there is no path, or sight is blocked, `2` a usage error such as a malformed coordinate.

Map file format:

| Line | Meaning |
| --- | --- |
| `layout odd-r` | Required. One of `odd-r`, `even-r`, `odd-q`, `even-q`. |
| `terrain C COST NAME` | Defines terrain character `C`. `COST` is a positive whole number, or `-` for impassable. |
| `opaque C` | Terrain `C` blocks line of sight. |
| `grid` | Starts the grid. Every line after it is one row, with one character per hex. |
| `# ...` | Comment, allowed before `grid`. |

## How to test

```sh
python3 -m unittest discover -s tests -v
```

The same tests also run under pytest:

```sh
python3 -m pytest tests
```

The tests cover coordinate round-trips across all four layouts, neighbour and distance invariants, ring and range sizes, line endpoints, map parsing with exact error positions, shortest path against the reach calculation, sight blocking, SVG well-formedness, ASCII markers, and the command-line exit codes.

## Project layout

```text
hexgrid.py            the whole tool: coordinates, maps, pathing, rendering, CLI
examples/valley.hex   sample terrain map
tests/test_hexgrid.py unit and command-line tests
```

## Notes

- Hexes are pointy-topped for `odd-r` and `even-r`, and flat-topped for `odd-q` and `even-q`. The SVG output follows the same orientation.
- The ASCII picture is a schematic for reading a map in a terminal, not an exact drawing.
- A cell outside the map counts as blocking for line of sight, because the tool cannot see past the edge of the map.
