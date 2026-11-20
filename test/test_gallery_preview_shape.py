"""Gallery previews show the whole picture (2026-11-08, friction item 49): every tile used to be a fixed 200 x 200 square (140 on a phone) and drew
its picture with object-fit: cover, so portrait art (most of ours is 768x1152) lost its head or feet and could not be judged at a glance. The thumbnail
files are already aspect-preserving, so the fix is only CSS: rows are as tall as their pictures, nothing is cropped, and a tile without a picture yet
keeps a 2:3 shape so the grid does not collapse. These checks read style.css as text (like test_mobile_layout.py); the real-page measurement of a
portrait, a landscape and a square picture is recorded on the task. No browser, no ComfyUI, no GPU."""
import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


def media_block(css, query="(max-width: 860px)"):
    start = css.find("@media " + query)
    assert start >= 0, "no @media %s block in style.css" % query
    i = css.index("{", start)
    depth, j = 0, i
    while j < len(css):
        depth += css[j] == "{"
        depth -= css[j] == "}"
        if depth == 0:
            break
        j += 1
    return css[i + 1:j]


def rule(css, selector):
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else None


def rule_bodies(css, selector):
    """Whitespace-stripped bodies of every rule whose comma-separated selector list contains exactly `selector`."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        sels = [s.strip() for s in re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S).split(",")]
        if selector in sels:
            out.append(re.sub(r"\s+", "", m.group(2)))
    return out


class GalleryPreviewShapeTest(unittest.TestCase):
    def test_rows_follow_the_pictures_on_a_desktop_and_on_a_phone(self):
        css = read("style.css")
        for where, block in (("desktop", css), ("phone", media_block(css))):
            gallery = rule(block, "#gallery")
            self.assertIsNotNone(gallery, where)
            self.assertNotRegex(gallery, r"grid-auto-rows:\s*\d+px", where + ": a fixed row height would crop or letterbox the pictures")
            # min-content, not auto: with auto the phone's flexible columns gave rows shorter than their tiles and the tiles overlapped
            self.assertRegex(gallery, r"grid-auto-rows:\s*min-content", where)
            self.assertRegex(gallery, r"align-items:\s*start", where)           # a short picture does not stretch to its row's tallest neighbour

    def test_the_picture_is_never_cropped_to_fill_its_tile(self):
        img = rule(read("style.css"), ".tile img")
        self.assertIsNotNone(img)
        self.assertNotRegex(img, r"object-fit:\s*cover")
        self.assertRegex(img, r"width:\s*100%")
        self.assertRegex(img, r"height:\s*auto")

    def test_a_tile_without_a_picture_yet_keeps_a_2_by_3_shape(self):
        shape = rule(read("style.css"), ".tile.loading, .tile[data-job]")
        self.assertIsNotNone(shape, "the loading and running tiles need a default shape")
        self.assertRegex(shape, r"aspect-ratio:\s*2\s*/\s*3")

    def test_the_page_load_skeleton_takes_up_its_own_grid_space(self):
        # a page-load skeleton tile used to be an absolute overlay with inset:0, so it covered the whole page instead of sitting in the grid
        bodies = rule_bodies(read("style.css"), ".tile.skel")
        self.assertTrue(bodies, "no rule for .tile.skel")
        for body in bodies:
            self.assertNotIn("position:absolute", body, ".tile.skel must not be absolutely positioned")
            self.assertNotIn("inset", body, ".tile.skel must not use inset")
        self.assertTrue(any("position:relative" in b for b in bodies), ".tile.skel needs position: relative")
        self.assertTrue(any("aspect-ratio:2/3" in b for b in bodies), ".tile.skel needs aspect-ratio: 2 / 3")

    def test_the_loading_overlay_still_covers_its_tile(self):
        bodies = rule_bodies(read("style.css"), ".tile.loading::before")
        self.assertTrue(bodies, "no rule for .tile.loading::before")
        self.assertTrue(any("position:absolute" in b for b in bodies))
        self.assertTrue(any("inset:0" in b for b in bodies))
        self.assertTrue(any('content:""' in b for b in bodies))

    def test_the_hover_menu_and_the_progress_bar_are_still_in_place(self):
        css = read("style.css")
        self.assertRegex(rule(css, ".tile .menu") or "", r"position:\s*absolute")
        self.assertRegex(rule(css, ".tile:hover .menu") or "", r"opacity:\s*1")
        self.assertRegex(rule(css, ".tile .bar") or "", r"position:\s*absolute")

    def test_the_phone_columns_are_still_the_smaller_ones(self):
        self.assertRegex(rule(media_block(read("style.css")), "#gallery") or "", r"minmax\(\s*1\d\dpx")


if __name__ == "__main__":
    unittest.main()
