import unittest
from pathlib import Path


class DeploymentArtifactTests(unittest.TestCase):
    def test_compose_mounts_databases_read_only_and_has_healthcheck(self):
        text = Path("compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/data/base/disclosure.sqlite:ro", text)
        self.assertIn("/data/agent:ro", text)
        self.assertIn("healthcheck:", text)
        self.assertNotIn("CLOVASTUDIO_API_KEY=", text)

    def test_dockerfile_does_not_copy_local_data(self):
        self.assertIn("data/", Path(".dockerignore").read_text(encoding="utf-8"))
        self.assertNotIn("D:\\", Path("Dockerfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
