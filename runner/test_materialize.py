from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from materialize import materialize, tree_digest


class MaterializeTests(TestCase):
    def test_replaces_stale_tree_and_verifies_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "kept.py").write_text("fresh\n")
            (target / "kept.py").write_text("old\n")
            (target / "stale.py").write_text("must disappear\n")

            digest = materialize(source, target, root)

            self.assertEqual(digest, tree_digest(source))
            self.assertEqual((target / "kept.py").read_text(), "fresh\n")
            self.assertFalse((target / "stale.py").exists())

    def test_refuses_target_outside_allowed_parent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "refusing to replace"):
                materialize(source, root / "nested" / "target", root)


if __name__ == "__main__":
    main()
