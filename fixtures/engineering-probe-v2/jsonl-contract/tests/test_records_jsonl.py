import unittest

from records_jsonl import dump_records, load_records


class RecordsJsonlTests(unittest.TestCase):
    def test_round_trips_simple_records(self):
        records = [
            {"id": "1", "note": "first"},
            {"id": "2", "note": "second"},
        ]

        self.assertEqual(load_records(dump_records(records)), records)

    def test_empty_input_is_an_empty_sequence(self):
        self.assertEqual(dump_records([]), "")
        self.assertEqual(load_records(""), [])


if __name__ == "__main__":
    unittest.main()
