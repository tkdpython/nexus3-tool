import unittest
from datetime import datetime

from click.testing import CliRunner

from nexus3_tool import cli


class HelperTests(unittest.TestCase):
    def test_sum_unique_asset_usage_dedupes_shared_assets(self):
        rows = [
            {"size": 300, "asset_usage": [("manifest-a", 100), ("shared-layer", 200)]},
            {"size": 500, "asset_usage": [("manifest-b", 300), ("shared-layer", 200)]},
        ]

        self.assertEqual(cli._sum_unique_asset_usage(rows), 600)

    def test_parse_tag_list_removes_blanks_and_duplicates(self):
        self.assertEqual(cli._parse_tag_list("old, dev-1,old,, latest "), ["old", "dev-1", "latest"])

    def test_find_delete_components_includes_same_manifest_aliases(self):
        components = [
            {"id": "1", "version": "old", "assets": [{"path": "v2/app/manifests/old", "checksum": {"sha256": "same"}}]},
            {"id": "2", "version": "alias", "assets": [{"path": "v2/app/manifests/alias", "checksum": {"sha256": "same"}}]},
            {"id": "3", "version": "keep", "assets": [{"path": "v2/app/manifests/keep", "checksum": {"sha256": "other"}}]},
        ]

        selected = cli._find_delete_components(components, ["old"])

        self.assertEqual([comp["version"] for comp in selected], ["alias", "old"])


class FakeClient(object):
    def list_docker_images(self, repo_name, name=None):
        self.repo_name = repo_name
        self.name = name
        return [
            {"name": "team-a/api", "tag": "1", "published": datetime(2026, 6, 30, 10, 0, 0), "size": 1024},
            {"name": "team-a/web", "tag": "2", "published": datetime.min, "size": 2048},
        ]

    def get_repository_blob_store_name(self, repo_name):
        return "default"

    def get_blob_store(self, name):
        return {"name": name, "totalSize": 4096, "availableSpace": 8192}


class DeleteFakeClient(object):
    def __init__(self):
        self.deleted_ids = []

    def get_image_components(self, repo_name, image_name):
        self.repo_name = repo_name
        self.image_name = image_name
        return [
            {
                "id": "1",
                "name": "myapp",
                "version": "old",
                "assets": [
                    {"path": "v2/myapp/manifests/old", "checksum": {"sha256": "manifest-old"}, "fileSize": 100},
                    {"path": "v2/myapp/blobs/shared", "checksum": {"sha256": "shared-layer"}, "fileSize": 200},
                ],
            },
            {
                "id": "2",
                "name": "myapp",
                "version": "alias",
                "assets": [
                    {"path": "v2/myapp/manifests/alias", "checksum": {"sha256": "manifest-old"}, "fileSize": 100},
                    {"path": "v2/myapp/blobs/shared", "checksum": {"sha256": "shared-layer"}, "fileSize": 200},
                ],
            },
            {
                "id": "3",
                "name": "myapp",
                "version": "keep",
                "assets": [{"path": "v2/myapp/manifests/keep", "checksum": {"sha256": "manifest-keep"}, "fileSize": 999}],
            },
        ]

    def delete_component(self, component_id):
        self.deleted_ids.append(component_id)

    def get_repository_blob_store_name(self, repo_name):
        return "default"

    def get_blob_store(self, name):
        return {"name": name, "totalSize": 4096, "availableSpace": 8192}


class CliTests(unittest.TestCase):
    def test_list_docker_images_outputs_size_and_blob_store_summary(self):
        fake = FakeClient()
        original_get_client = cli._get_client
        cli._get_client = lambda: fake
        try:
            result = CliRunner().invoke(cli.main, ["list-docker-images", "docker-hosted", "--image-name", "team-a/*"])
        finally:
            cli._get_client = original_get_client

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(fake.repo_name, "docker-hosted")
        self.assertEqual(fake.name, "team-a/*")
        self.assertIn("SIZE", result.output)
        self.assertIn("team-a/api:1", result.output)
        self.assertIn("Matched tags: 2", result.output)
        self.assertIn("Matched image disk usage: 3.00 KiB", result.output)
        self.assertIn("Nexus blob store: default", result.output)
        self.assertIn("Blob store used: 4.00 KiB", result.output)
        self.assertIn("Blob store available: 8.00 KiB", result.output)

    def test_delete_docker_images_quiet_deletes_requested_tag_and_alias(self):
        fake = DeleteFakeClient()
        original_get_client = cli._get_client
        cli._get_client = lambda: fake
        try:
            result = CliRunner().invoke(
                cli.main,
                ["delete-docker-images", "docker-hosted", "--image-name", "myapp", "--tags", "old", "--quiet"],
            )
        finally:
            cli._get_client = original_get_client

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(fake.repo_name, "docker-hosted")
        self.assertEqual(fake.image_name, "myapp")
        self.assertEqual(fake.deleted_ids, ["2", "1"])
        self.assertIn("myapp:alias", result.output)
        self.assertIn("[same image as requested tag]", result.output)
        self.assertIn("myapp:old", result.output)
        self.assertIn("Space freed by successful deletes: 300 B", result.output)
        self.assertIn("Blob store available: 8.00 KiB", result.output)

    def test_delete_docker_images_prompts_by_default(self):
        fake = DeleteFakeClient()
        original_get_client = cli._get_client
        cli._get_client = lambda: fake
        try:
            result = CliRunner().invoke(
                cli.main,
                ["delete-docker-images", "docker-hosted", "--image-name", "myapp", "--tags", "old"],
                input="n\n",
            )
        finally:
            cli._get_client = original_get_client

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Delete 2 tag(s)?", result.output)
        self.assertEqual(fake.deleted_ids, [])


if __name__ == "__main__":
    unittest.main()
