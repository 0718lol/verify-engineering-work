"""In-memory job reservation store."""

import time


class ReservationStore:
    def __init__(self):
        self._reserved = set()

    def reserve(self, job_id):
        if job_id in self._reserved:
            return False
        time.sleep(0.005)
        self._reserved.add(job_id)
        return True
