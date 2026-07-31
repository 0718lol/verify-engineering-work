import json
import unittest

from records_jsonl import dump_records, load_records


class HiddenRecordsJsonlTests(unittest.TestCase):
    def test_output_is_json_lines_and_round_trips_arbitrary_text(self):
        records = [
            {
                "id": "alpha|1",
                "note": "line one\nline two",
                "label": "上海",
                "future": {"enabled": True},
            },
            {"id": "beta", "note": ""},
        ]

        rendered = dump_records(records)
        self.assertTrue(rendered.endswith("\n"))
        decoded_lines = [json.loads(line) for line in rendered.splitlines()]
        self.assertEqual(decoded_lines, records)
        self.assertEqual(load_records(rendered), records)


if __name__ == "__main__":
    unittest.main()
