import threading
import unittest

from reservations import ReservationStore


class ReservationTests(unittest.TestCase):
    def test_only_one_worker_reserves_a_job(self):
        store = ReservationStore()
        barrier = threading.Barrier(12)
        results = []

        def reserve():
            barrier.wait()
            results.append(store.reserve("job-1"))

        threads = [threading.Thread(target=reserve) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(True), 1)


if __name__ == "__main__":
    unittest.main()
