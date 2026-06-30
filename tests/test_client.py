import unittest
from datetime import datetime

from nexus3_tool.client import Nexus3Client, _get_component_size, _has_wildcards


class FakeClient(Nexus3Client):
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def _iter_pages(self, path, params=None):
        self.calls.append((path, params))
        for item in self.pages:
            yield item


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
        self.assertEqual(client.calls[0][1]["name"], "service-api")
        self.assertEqual(rows[0]["size"], 1024)
        self.assertEqual(rows[0]["asset_usage"], [(None, 1024)])
        self.assertEqual(rows[0]["published"], datetime(2026, 6, 30, 10, 0, 0))

    def test_list_docker_images_filters_wildcards_client_side(self):
        client = FakeClient([
            {"name": "team-a/api", "version": "1", "assets": [{"fileSize": 100}]},
            {"name": "team-a/web", "version": "1", "assets": [{"fileSize": 200}]},
            {"name": "team-b/api", "version": "1", "assets": [{"fileSize": 300}]},
        ])

        rows = client.list_docker_images("docker-hosted", name="team-a/*")

        self.assertEqual(client.calls[0][0], "/service/rest/v1/components")
        self.assertEqual([row["name"] for row in rows], ["team-a/api", "team-a/web"])
        self.assertEqual(sum(row["size"] for row in rows), 300)


if __name__ == "__main__":
    unittest.main()
