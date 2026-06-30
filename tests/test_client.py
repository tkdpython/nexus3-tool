import unittest
from datetime import datetime

from nexus3_tool.client import Nexus3Client, _get_component_size, _has_wildcards


class FakeClient(Nexus3Client):
    def __init__(self, pages, manifests=None):
        self.pages = pages
        self.manifests = manifests or {}
        self.calls = []
        self.manifest_urls = []

    def _iter_pages(self, path, params=None):
        self.calls.append((path, params))
        for item in self.pages:
            yield item

    def _get_manifest_json(self, url):
        self.manifest_urls.append(url)
        return self.manifests[url]


class ClientTests(unittest.TestCase):
    def test_component_size_sums_asset_file_sizes(self):
        component = {
            "assets": [
                {"fileSize": 1024},
                {"fileSize": "2048"},
                {"fileSize": None},
                {"fileSize": "not-a-number"},
            ]
        }

        self.assertEqual(_get_component_size(component), 3072)

    def test_has_wildcards_detects_star_and_question_mark(self):
        self.assertTrue(_has_wildcards("team/*"))
        self.assertTrue(_has_wildcards("service-?"))
        self.assertFalse(_has_wildcards("service-api"))
        self.assertFalse(_has_wildcards(None))

    def test_list_docker_images_uses_search_for_exact_name(self):
        client = FakeClient([
            {
                "name": "service-api",
                "version": "1.0.0",
                "assets": [{"lastModified": "2026-06-30T10:00:00Z", "fileSize": 1024}],
            }
        ])

        rows = client.list_docker_images("docker-hosted", name="service-api")

        self.assertEqual(client.calls[0][0], "/service/rest/v1/search")
        self.assertEqual(client.calls[1][0], "/service/rest/v1/components")
        self.assertEqual(client.calls[0][1]["name"], "service-api")
        self.assertEqual(rows[0]["metadata_size"], 1024)
        self.assertEqual(rows[0]["asset_usage"], [(None, 1024)])
        self.assertEqual(rows[0]["published"], datetime(2026, 6, 30, 10, 0, 0))

    def test_list_docker_images_does_not_fetch_manifests_for_size(self):
        manifest_url = "https://nexus.example/repository/docker/v2/service-api/manifests/1.0.0"
        client = FakeClient(
            [
                {
                    "name": "service-api",
                    "version": "1.0.0",
                    "assets": [
                        {
                            "path": "v2/service-api/manifests/1.0.0",
                            "downloadUrl": manifest_url,
                            "lastModified": "2026-06-30T10:00:00Z",
                            "fileSize": 1800,
                            "checksum": {"sha256": "manifest-json"},
                        }
                    ],
                }
            ],
            manifests={
                manifest_url: {
                    "schemaVersion": 2,
                    "config": {"digest": "sha256:config", "size": 1234},
                    "layers": [
                        {"digest": "sha256:layer1", "size": 10000000},
                        {"digest": "sha256:layer2", "size": 20000000},
                    ],
                }
            },
        )

        rows = client.list_docker_images("docker-hosted", name="service-api")

        self.assertEqual(client.manifest_urls, [])
        self.assertEqual(rows[0]["metadata_size"], 1800)
        self.assertEqual(rows[0]["asset_usage"], [("manifest-json", 1800)])

    def test_list_docker_images_filters_wildcards_client_side(self):
        client = FakeClient([
            {"name": "team-a/api", "version": "1", "assets": [{"fileSize": 100}]},
            {"name": "team-a/web", "version": "1", "assets": [{"fileSize": 200}]},
            {"name": "team-b/api", "version": "1", "assets": [{"fileSize": 300}]},
        ])

        rows = client.list_docker_images("docker-hosted", name="team-a/*")

        self.assertEqual(client.calls[0][0], "/service/rest/v1/components")
        self.assertEqual([row["name"] for row in rows], ["team-a/api", "team-a/web"])
        self.assertEqual(sum(row["metadata_size"] for row in rows), 300)
    def test_list_docker_images_falls_back_to_components_when_search_is_empty(self):
        class FallbackClient(FakeClient):
            def _iter_pages(self, path, params=None):
                self.calls.append((path, params))
                if path == "/service/rest/v1/search":
                    return
                for item in self.pages:
                    yield item

        client = FallbackClient([
            {"name": "fresh-image", "version": "1", "assets": [{"fileSize": 100}]},
            {"name": "other-image", "version": "1", "assets": [{"fileSize": 200}]},
        ])

        rows = client.list_docker_images("docker-hosted", name="fresh-image")

        self.assertEqual(client.calls[0][0], "/service/rest/v1/search")
        self.assertEqual(client.calls[1][0], "/service/rest/v1/components")
        self.assertEqual([row["name"] for row in rows], ["fresh-image"])

    def test_get_image_components_falls_back_to_components_when_search_is_empty(self):
        class FallbackClient(FakeClient):
            def _iter_pages(self, path, params=None):
                self.calls.append((path, params))
                if path == "/service/rest/v1/search":
                    return
                for item in self.pages:
                    yield item

        client = FallbackClient([
            {"name": "fresh-image", "version": "1", "assets": [{"fileSize": 100}]},
            {"name": "other-image", "version": "1", "assets": [{"fileSize": 200}]},
        ])

        components = client.get_image_components("docker-hosted", "fresh-image")

        self.assertEqual(client.calls[0][0], "/service/rest/v1/search")
        self.assertEqual(client.calls[1][0], "/service/rest/v1/components")
        self.assertEqual([comp["name"] for comp in components], ["fresh-image"])


if __name__ == "__main__":
    unittest.main()
