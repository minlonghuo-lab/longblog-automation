import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service"))
from asset_cleanup import prune_unused_assets


class AssetCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "public" / "trilium-assets"
        self.root.mkdir(parents=True)

    def asset(self, note, number, filename=None):
        file = self.root / note / (filename or f"{number:016x}.png")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"image")
        return file

    def url(self, file):
        return "/trilium-assets/" + file.relative_to(self.root).as_posix()

    def test_prunes_only_obsolete_generated_files_and_is_repeatable(self):
        keep = self.asset("A", 1)
        old = self.asset("A", 2)
        manual = self.asset("A", 0, "manual.png")
        other_note = self.asset("NotInSnapshot", 3)
        brand = self.base / "public" / "brand" / "avatar.png"
        brand.parent.mkdir()
        brand.write_bytes(b"brand")
        posts = [{"id": "A", "contentHtml": f'<img src="{self.url(keep)}">'}]
        self.assertEqual(prune_unused_assets(posts, self.root), [self.url(old)])
        self.assertTrue(keep.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(other_note.exists())
        self.assertEqual(brand.read_bytes(), b"brand")
        self.assertEqual(prune_unused_assets(posts, self.root), [])

    def test_references_from_other_articles_protect_shared_attachments(self):
        shared = self.asset("A", 1)
        old = self.asset("A", 2)
        posts = [{"id": "A", "contentHtml": "plain text"},
                 {"id": "B", "contentHtml": f'<a href="{self.url(shared)}">shared</a>'}]
        self.assertEqual(prune_unused_assets(posts, self.root), [self.url(old)])
        self.assertTrue(shared.exists())

    def test_query_strings_entities_srcsets_and_css_references_are_preserved(self):
        files = [self.asset("A", n) for n in range(1, 5)]
        html = (f'<img src="https://blog.test{self.url(files[0])}?a=1&amp;b=2">'
                f'<img srcset="{self.url(files[1])} 1x, {self.url(files[2])} 2x">'
                f'<div style="background:url(\'{self.url(files[3])}\')"></div>')
        self.assertEqual(prune_unused_assets([{"id": "A", "contentHtml": html}], self.root), [])
        self.assertTrue(all(file.exists() for file in files))

    def test_missing_reference_fails_before_any_file_is_deleted(self):
        old = self.asset("A", 2)
        with self.assertRaises(ValueError):
            prune_unused_assets([{"id": "A", "contentHtml": '/trilium-assets/A/0000000000000001.png'}], self.root)
        self.assertTrue(old.exists())

    def test_invalid_records_and_urls_fail_closed(self):
        old = self.asset("A", 2)
        for record in [{"id": "../brand", "contentHtml": ""},
                       {"id": "A"},
                       {"id": "A", "contentHtml": '/trilium-assets/A/../outside.png'}]:
            with self.subTest(record=record), self.assertRaises(ValueError):
                prune_unused_assets([record], self.root)
            self.assertTrue(old.exists())

    def test_empty_snapshot_does_not_delete_assets(self):
        old = self.asset("A", 2)
        self.assertEqual(prune_unused_assets([], self.root), [])
        self.assertTrue(old.exists())

    def test_symbolic_links_are_never_followed_or_deleted(self):
        outside = self.base / "outside"
        outside.mkdir()
        target = outside / "0000000000000001.png"
        target.write_bytes(b"outside")
        link_dir = self.root / "Linked"
        link_dir.symlink_to(outside, target_is_directory=True)
        (self.root / "A").mkdir()
        link_file = self.root / "A" / "0000000000000002.png"
        link_file.symlink_to(target)
        posts = [{"id": "Linked", "contentHtml": ""}, {"id": "A", "contentHtml": ""}]
        self.assertEqual(prune_unused_assets(posts, self.root), [])
        self.assertTrue(link_file.is_symlink())
        self.assertEqual(target.read_bytes(), b"outside")


if __name__ == "__main__":
    unittest.main()
