"""Phone layout of the PromptForge web UI (2026-10-14): the page was a left input panel with a hard 400 px minimum width beside the gallery, on a
body with overflow hidden and no phone styles, so on a ~400 px phone the input panel filled the screen and pushed the image library off the right
edge with nothing to scroll to ("I can't see the image library at all"). These checks pin the phone rules: a Create/Gallery switch, one panel at a
time, and a lightbox that stacks. They read the static files (no browser): the real-browser check is in the PR.

Run: .venv/bin/python -m unittest discover -s test   (or: python -m unittest test.test_mobile_layout)
"""
import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def read(name):
    with open(os.path.join(WEB, name), encoding="utf-8") as f:
        return f.read()


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


class MobileLayoutTest(unittest.TestCase):
    def test_the_phone_block_lets_the_input_panel_shrink_to_the_screen(self):
        inputs = rule(media_block(read("style.css")), "#inputs")
        self.assertIsNotNone(inputs, "#inputs has a rule in the phone block")
        self.assertRegex(inputs, r"min-width:\s*0")
        self.assertRegex(inputs, r"width:\s*100%")

    def test_on_a_phone_the_panels_stack_and_only_one_shows_at_a_time(self):
        css = read("style.css")
        phone = media_block(css)
        self.assertRegex(rule(phone, "body") or "", r"flex-direction:\s*column")
        self.assertRegex(rule(phone, "body.view-gallery #inputs") or "", r"display:\s*none")
        self.assertRegex(rule(phone, "body:not(.view-gallery) #gallery") or "", r"display:\s*none")

    def test_the_gallery_grid_uses_smaller_tiles_on_a_phone(self):
        gallery = rule(media_block(read("style.css")), "#gallery")
        self.assertIsNotNone(gallery)
        self.assertRegex(gallery, r"minmax\(\s*1\d\dpx")

    def test_the_view_switch_is_hidden_on_a_desktop_and_shown_on_a_phone(self):
        css = read("style.css")
        self.assertRegex(rule(css, "#viewBar") or "", r"display:\s*none")           # the base rule: not on a desktop
        self.assertRegex(rule(media_block(css), "#viewBar") or "", r"display:\s*flex")

    def test_the_view_switch_is_at_the_top_of_the_page_not_fixed_to_the_bottom(self):
        # a phone's address bar can sit at the BOTTOM of the screen (and a plugin frame sized with 100vh runs under it): a bar fixed to the
        # bottom edge was covered by it (reported 2026-10-14), so the bar is the first row of the page instead
        phone = media_block(read("style.css"))
        bar = rule(phone, "#viewBar") or ""
        self.assertRegex(bar, r"order:\s*-1")
        self.assertNotRegex(bar, r"position:\s*fixed")
        self.assertNotRegex(bar, r"(?<![-\w])bottom:")      # a positioned bottom edge (border-bottom is fine)
        self.assertNotRegex(rule(phone, "body") or "", r"padding-bottom")

    def test_the_page_has_the_switch_with_both_views_and_the_script_drives_it(self):
        html, js = read("index.html"), read("app.js")
        self.assertIn('id="viewBar"', html)
        self.assertIn('data-view="create"', html)
        self.assertIn('data-view="gallery"', html)
        self.assertIn("view-gallery", js)

    def test_the_lightbox_stacks_on_a_phone_instead_of_squeezing_the_info_beside_the_image(self):
        phone = media_block(read("style.css"))
        self.assertRegex(rule(phone, "#lightbox") or "", r"flex-direction:\s*column")
        self.assertRegex(rule(phone, "#lightbox .info") or "", r"max-width:\s*100%")

    def test_the_regenerate_panel_exists_and_fills_the_screen_width_on_a_phone(self):
        html, js, css = read("index.html"), read("app.js"), read("style.css")
        for need in ('id="regenPanel"', 'id="regenPrompt"', 'id="regenDenoise"', 'id="regenModel"', 'id="regenRun"'):
            self.assertIn(need, html)
        self.assertRegex(html, r'id="regenDenoise"[^>]*min="0.3"[^>]*max="1"[^>]*step="0.05"')
        self.assertIn("from_gallery", js)                  # the picture is read on the server, not uploaded by the page
        self.assertIn("/api/regenerate", js)
        self.assertRegex(rule(media_block(css), "#regenPanel .card") or "", r"width:\s*100%")
        start = js.index("// ---- regenerate from a gallery image")
        block = js[start:js.index("// ---- paged gallery", start)]
        self.assertNotRegex(block, r"setInterval|setTimeout")   # the panel adds no timers


if __name__ == "__main__":
    unittest.main()
