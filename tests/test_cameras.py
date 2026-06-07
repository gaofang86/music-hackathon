import unittest
from unittest.mock import patch

from accessible_ensemble.cameras import CameraCandidate, resolve_camera


class CameraSelectionTests(unittest.TestCase):
    def test_explicit_camera_skips_discovery(self):
        with patch("accessible_ensemble.cameras.discover_cameras") as discover:
            self.assertEqual(resolve_camera("3", "performer"), 3)
            discover.assert_not_called()

    def test_only_camera_is_selected_automatically(self):
        candidate = CameraCandidate(2, 1920, 1080, True)
        with patch(
            "accessible_ensemble.cameras.discover_cameras",
            return_value=[candidate],
        ):
            self.assertEqual(resolve_camera("select", "conductor"), 2)

    def test_missing_camera_raises(self):
        with patch(
            "accessible_ensemble.cameras.discover_cameras",
            return_value=[],
        ):
            with self.assertRaises(RuntimeError):
                resolve_camera("select", "performer")


if __name__ == "__main__":
    unittest.main()
