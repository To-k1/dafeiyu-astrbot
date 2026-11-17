import tempfile
import unittest
from pathlib import Path

from deploy.dsh_bridge.project_compute import checkout, output_baselines, prepare_wheels, project_directory, publish_outputs, remove_file


class ProjectComputeTests(unittest.TestCase):
    def test_only_explicit_outputs_are_published_after_project_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "stage"
            stage.mkdir()
            project = project_directory(root, "Project1", create=True)
            original = project / "notes.txt"
            original.write_text("keep", encoding="utf-8")
            baseline = output_baselines(project, ["output/result.txt"])
            checkout(project, stage)
            (stage / "notes.txt").unlink()
            (stage / "output").mkdir()
            (stage / "output/result.txt").write_text("test passed", encoding="utf-8")
            (stage / "unlisted.txt").touch()
            self.assertEqual(publish_outputs(stage, project, baseline), ["output/result.txt"])
            self.assertEqual(original.read_text(encoding="utf-8"), "keep")
            self.assertFalse((project / "unlisted.txt").exists())

    def test_rejects_traversal_writes_and_racing_project_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                project_directory(root, "../Outside", create=True)
            with self.assertRaises(ValueError):
                output_baselines(root, ["../other.txt"])
            with self.assertRaises(ValueError):
                prepare_wheels(root, ["https://example.com/package.whl"])
            project = project_directory(root, "Project1", create=True)
            stage = root / "stage"
            stage.mkdir()
            (project / "out.txt").write_text("old", encoding="utf-8")
            baseline = output_baselines(project, ["out.txt"])
            (project / "out.txt").write_text("changed by owner", encoding="utf-8")
            (stage / "out.txt").write_text("result", encoding="utf-8")
            with self.assertRaises(ValueError):
                publish_outputs(stage, project, baseline)
            self.assertEqual((project / "out.txt").read_text(encoding="utf-8"), "changed by owner")
            (project / "folder").mkdir()
            (project / "folder/keep.txt").touch()
            with self.assertRaises(ValueError):
                remove_file(project, "folder")
            self.assertTrue((project / "folder/keep.txt").is_file())
            self.assertEqual(remove_file(project, "out.txt"), {"status": "removed", "file": "out.txt"})
