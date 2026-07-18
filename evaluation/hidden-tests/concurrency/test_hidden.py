import threading
import unittest

from reservations import ReservationStore


class HiddenReservationTests(unittest.TestCase):
    def test_race_is_idempotent_for_multiple_job_ids(self):
        store = ReservationStore()
        barrier = threading.Barrier(20)
        results = {"job-a": [], "job-b": []}

        def reserve(job_id):
            barrier.wait()
            results[job_id].append(store.reserve(job_id))

        threads = [
            threading.Thread(target=reserve, args=(f"job-{letter}",))
            for letter in ("a", "b")
            for _ in range(10)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results["job-a"].count(True), 1)
        self.assertEqual(results["job-b"].count(True), 1)
        self.assertFalse(store.reserve("job-a"))
        self.assertTrue(store.reserve("job-c"))


if __name__ == "__main__":
    unittest.main()
