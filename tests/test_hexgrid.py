import io
import sys
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import hexgrid as hg  # noqa: E402

VALLEY = ROOT / "examples" / "valley.hex"


def small_cells(radius=3):
    return [(q, r) for q in range(-radius, radius + 1)
            for r in range(-radius, radius + 1) if abs(q + r) <= radius]


def parse_ok(text):
    m, errors = hg.parse_map(text)
    if errors:
        raise AssertionError(f"unexpected errors: {errors}")
    return m


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = hg.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CoordinateTests(unittest.TestCase):
    def test_offset_axial_round_trip_all_layouts(self):
        for layout in hg.LAYOUTS:
            for col in range(-5, 6):
                for row in range(-5, 6):
                    q, r = hg.offset_to_axial(col, row, layout)
                    self.assertEqual(hg.axial_to_offset(q, r, layout), (col, row), layout)

    def test_neighbors_are_six_distinct_unit_steps(self):
        for h in small_cells():
            ns = hg.neighbors(h)
            self.assertEqual(len(set(ns)), 6)
            for n in ns:
                self.assertEqual(hg.distance(h, n), 1)

    def test_distance_is_symmetric_and_obeys_triangle_inequality(self):
        cells = small_cells(2)
        for a in cells:
            for b in cells:
                self.assertEqual(hg.distance(a, b), hg.distance(b, a))
                for c in cells:
                    self.assertLessEqual(hg.distance(a, c), hg.distance(a, b) + hg.distance(b, c))

    def test_cube_coordinates_sum_to_zero(self):
        for h in small_cells():
            x, y, z = hg.to_cube(h)
            self.assertEqual(x + y + z, 0)

    def test_ring_has_six_radius_cells_all_at_that_distance(self):
        centre = (2, -1)
        for radius in range(1, 5):
            cells = hg.ring(centre, radius)
            self.assertEqual(len(cells), 6 * radius)
            self.assertEqual(len(set(cells)), 6 * radius)
            for c in cells:
                self.assertEqual(hg.distance(centre, c), radius)

    def test_range_is_union_of_rings_and_has_hexagonal_size(self):
        centre = (0, 0)
        for radius in range(0, 5):
            cells = hg.hex_range(centre, radius)
            self.assertEqual(len(cells), 3 * radius * (radius + 1) + 1)
            ring_union = set([centre])
            for k in range(1, radius + 1):
                ring_union.update(hg.ring(centre, k))
            self.assertEqual(set(cells), ring_union)

    def test_line_runs_end_to_end_one_neighbour_at_a_time(self):
        cells = small_cells(2)
        for a in cells:
            for b in cells:
                path = hg.hex_line(a, b)
                self.assertEqual(path[0], a)
                self.assertEqual(path[-1], b)
                self.assertEqual(len(path), hg.distance(a, b) + 1)
                for x, y in zip(path, path[1:]):
                    self.assertEqual(hg.distance(x, y), 1)

    def test_line_to_self_is_single_cell(self):
        self.assertEqual(hg.hex_line((3, -2), (3, -2)), [(3, -2)])


class MapParsingTests(unittest.TestCase):
    def test_example_map_parses_to_full_cell_set(self):
        m = hg.parse_map(VALLEY.read_text(encoding="utf-8"))[0]
        self.assertIsNotNone(m)
        self.assertEqual(m.layout, "odd-r")
        self.assertEqual(len(m.cells), 8 * 5)
        self.assertTrue(m.terrains["f"].opaque)
        self.assertIsNone(m.terrains["~"].cost)

    def test_lint_reports_exact_line_and_column_of_unknown_terrain(self):
        text = "layout odd-r\nterrain . 1 open\n# a comment\ngrid\n..x.\n"
        _, errors = hg.parse_map(text)
        self.assertEqual(errors, [(5, 3, "unknown terrain 'x' in grid")])

    def test_ragged_rows_and_bad_cost_are_both_reported(self):
        text = "layout odd-r\nterrain . 1 open\nterrain f 0 forest\ngrid\n..f\n..\n"
        m, errors = hg.parse_map(text)
        self.assertIsNone(m)
        self.assertIn((3, 11, "cost must be a positive whole number, or '-' for impassable"), errors)
        self.assertIn((6, 3, "row has 2 hexes, expected 3"), errors)
        self.assertEqual([line for line, _, _ in errors], sorted(line for line, _, _ in errors))

    def test_missing_layout_and_grid_are_reported(self):
        _, errors = hg.parse_map("terrain . 1 open\n")
        messages = [msg for _, _, msg in errors]
        self.assertIn("missing layout directive", messages)
        self.assertIn("missing grid section", messages)

    def test_unknown_directive_and_duplicate_terrain(self):
        text = "layout odd-r\nterrain . 1 open\nterrain . 2 again\nfrobnicate\ngrid\n.\n"
        _, errors = hg.parse_map(text)
        self.assertEqual(errors, [
            (3, 9, "duplicate terrain '.'"),
            (4, 1, "unknown directive 'frobnicate'"),
        ])


class PathingTests(unittest.TestCase):
    def setUp(self):
        self.m = parse_ok(VALLEY.read_text(encoding="utf-8"))

    def test_reachable_agrees_with_shortest_path_for_every_cell(self):
        start = hg.offset_to_axial(0, 0, self.m.layout)
        budget = 6
        best = hg.reachable(self.m, start, budget)
        self.assertEqual(best[start], 0)
        for h in self.m.cells:
            found = hg.shortest_path(self.m, start, h)
            if h in best:
                self.assertIsNotNone(found)
                self.assertEqual(found[0], best[h])
                self.assertLessEqual(best[h], budget)
            else:
                self.assertTrue(found is None or found[0] > budget)

    def test_shortest_path_cost_equals_sum_of_entered_cells(self):
        start = hg.offset_to_axial(0, 0, self.m.layout)
        goal = hg.offset_to_axial(7, 4, self.m.layout)
        total, path = hg.shortest_path(self.m, start, goal)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], goal)
        self.assertEqual(total, sum(self.m.cost_into(h) for h in path[1:]))
        for x, y in zip(path, path[1:]):
            self.assertEqual(hg.distance(x, y), 1)
            self.assertIsNotNone(self.m.cost_into(y))

    def test_wall_between_cells_makes_goal_unreachable(self):
        m = parse_ok("layout odd-r\nterrain . 1 open\nterrain # - wall\ngrid\n.#.\n")
        a = hg.offset_to_axial(0, 0, "odd-r")
        b = hg.offset_to_axial(2, 0, "odd-r")
        self.assertIsNone(hg.shortest_path(m, a, b))
        self.assertEqual(hg.reachable(m, a, 99), {a: 0})

    def test_forest_blocks_line_of_sight_but_open_ground_does_not(self):
        blocked = parse_ok("layout odd-r\nterrain . 1 open\nterrain f 2 forest\nopaque f\ngrid\n.f.\n")
        clear = parse_ok("layout odd-r\nterrain . 1 open\nterrain f 2 forest\nopaque f\ngrid\n...\n")
        a = hg.offset_to_axial(0, 0, "odd-r")
        b = hg.offset_to_axial(2, 0, "odd-r")
        visible, blocker = hg.line_of_sight(blocked, a, b)
        self.assertFalse(visible)
        self.assertEqual(blocker, hg.offset_to_axial(1, 0, "odd-r"))
        self.assertEqual(hg.line_of_sight(clear, a, b), (True, None))


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.m = parse_ok(VALLEY.read_text(encoding="utf-8"))

    def test_svg_is_well_formed_with_one_polygon_per_hex(self):
        route = hg.shortest_path(self.m, hg.offset_to_axial(0, 0, "odd-r"),
                                 hg.offset_to_axial(7, 4, "odd-r"))[1]
        root = ET.fromstring(hg.svg_map(self.m, route))
        polygons = [el for el in root.iter() if el.tag.endswith("polygon")]
        self.assertEqual(len(polygons), len(self.m.cells))
        self.assertEqual(root.tag.split("}")[-1], "svg")

    def test_ascii_marks_start_goal_and_route(self):
        start = hg.offset_to_axial(0, 0, "odd-r")
        goal = hg.offset_to_axial(7, 4, "odd-r")
        route = hg.shortest_path(self.m, start, goal)[1]
        picture = hg.ascii_map(self.m, route, start, goal)
        self.assertEqual(picture.count("S"), 1)
        self.assertEqual(picture.count("G"), 1)
        self.assertEqual(picture.count("*"), len(route) - 2)

    def test_flat_layout_picture_draws_every_hex(self):
        m = parse_ok("layout odd-q\nterrain . 1 open\ngrid\n...\n...\n")
        picture = hg.ascii_map(m)
        self.assertEqual(picture.count("."), 6)


class CommandLineTests(unittest.TestCase):
    def test_lint_passes_on_example_and_fails_on_bad_file(self):
        code, out, _ = run_cli("lint", str(VALLEY))
        self.assertEqual(code, 0)
        self.assertIn("ok:", out)

        bad = ROOT / "tests" / "_bad_tmp_map.hex"
        try:
            bad.write_text("layout odd-r\nterrain . 1 open\ngrid\n.z\n", encoding="utf-8")
            code, out, _ = run_cli("lint", str(bad))
            self.assertEqual(code, 1)
            self.assertIn(":4:2: error: unknown terrain 'z' in grid", out)
        finally:
            bad.unlink(missing_ok=True)

    def test_path_reports_no_path_with_exit_code_one(self):
        wall = ROOT / "tests" / "_wall_tmp_map.hex"
        try:
            wall.write_text("layout odd-r\nterrain . 1 open\nterrain # - wall\ngrid\n.#.\n", encoding="utf-8")
            code, out, _ = run_cli("path", str(wall), "0,0", "2,0")
            self.assertEqual(code, 1)
            self.assertIn("no path", out)
        finally:
            wall.unlink(missing_ok=True)

    def test_convert_prints_all_three_coordinate_forms(self):
        code, out, _ = run_cli("convert", "1,1", "--layout", "odd-r")
        self.assertEqual(code, 0)
        self.assertIn("offset  1,1 (odd-r)", out)
        self.assertIn("axial   1,1", out)
        self.assertIn("cube    1,-2,1", out)

    def test_bad_coordinate_is_a_usage_error(self):
        code, _, err = run_cli("convert", "banana")
        self.assertEqual(code, 2)
        self.assertIn("must look like COL,ROW", err)


if __name__ == "__main__":
    unittest.main()
