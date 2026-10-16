import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from PIL import Image
from core import repair, comfy


class RepairTest(unittest.TestCase):
    def test_work_size_snaps_to_8(self):
        w, h = repair.work_size(300, 410)
        self.assertEqual(max(w, h), 1024)
        self.assertEqual((w % 8, h % 8), (0, 0))

    def test_transparent_background_is_not_repainted(self):
        img = Image.new("RGBA", (64, 64), (200, 50, 50, 255))
        img.paste((0, 0, 0, 0), (0, 0, 16, 16))
        _, mask_png, _ = repair.prepare(img, Image.new("L", (64, 64), 0))
        import io
        self.assertLess(Image.open(io.BytesIO(mask_png)).getpixel((20, 20)), 5)

    def test_finish_only_changes_masked_pixels(self):
        orig = Image.new("RGBA", (64, 64), (200, 50, 50, 255))
        mask = Image.new("L", (64, 64), 0)
        mask.paste(255, (32, 0, 64, 64))
        out = repair.finish(orig, mask, Image.new("RGB", (128, 128), (0, 0, 255)), feather=0)
        self.assertEqual(out.getpixel((5, 5)), (200, 50, 50, 255))
        self.assertEqual(out.getpixel((60, 5)), (0, 0, 255, 255))

    def test_finish_keeps_background_transparent(self):
        orig = Image.new("RGBA", (32, 32), (10, 10, 10, 255))
        orig.paste((0, 0, 0, 0), (0, 0, 8, 8))
        out = repair.finish(orig, Image.new("L", (32, 32), 0), Image.new("RGB", (32, 32), (9, 9, 200)), feather=0)
        self.assertEqual(out.getpixel((2, 2))[3], 0)

    def test_graph_wires_mask_and_denoise(self):
        g = comfy.build_repair_graph({"id": "abcdefgh", "model": "m", "positive": "p", "negative": "n",
                                      "image_name": "i.png", "mask_name": "m.png", "seed": 1, "steps": 20,
                                      "cfg": 5, "sampler": "euler", "scheduler": "normal", "denoise": 0.6})
        self.assertEqual(g["11"]["inputs"]["denoise"], 0.6)
        self.assertEqual(g["11"]["inputs"]["latent_image"], ["24", 0])


if __name__ == "__main__":
    unittest.main()
