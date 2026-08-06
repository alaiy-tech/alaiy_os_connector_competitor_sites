"""Time budget and memory-pressure guard for a Deep scrape run.

Both exist for the same reason: on a 2 vCPU / ~2GB-available / no-swap box,
a run must always be able to stop cleanly (flush what it has, write a
Partial status) rather than either running into the RQ hard timeout mid-page
or getting OOM-killed. Every checkpoint in the runner goes through one of
these two objects so there's exactly one place that decides "keep going" vs
"wrap up now".
"""

import time
from collections import deque


class Budget:
    """Soft wall-clock budget, independent of (and smaller than) the RQ job
    timeout. `reserve_seconds` is held back so there's always time left for
    a final flush + log write even if the last page finished right at the
    edge of the budget."""

    def __init__(self, total_seconds, reserve_seconds=90):
        self.start = time.monotonic()
        self.total_seconds = total_seconds
        self.reserve_seconds = reserve_seconds
        self._page_durations = deque(maxlen=5)  # rolling window, used by afford()

    def elapsed(self):
        return time.monotonic() - self.start

    def remaining(self):
        if self.total_seconds <= 0:
            return float("inf")
        return max(0.0, self.total_seconds - self.elapsed())

    def expired(self):
        if self.total_seconds <= 0:
            return False
        return self.remaining() <= self.reserve_seconds

    def record_page_duration(self, seconds):
        self._page_durations.append(seconds)

    def afford_one_more_page(self):
        """Is there room for another page, given how long recent pages have
        taken? Stops one page early on a slow site rather than getting cut
        off mid-page by the hard RQ timeout."""
        if self.total_seconds <= 0:
            return True
        if not self._page_durations:
            typical = 5.0
        else:
            typical = sum(self._page_durations) / len(self._page_durations)
        return self.remaining() > (self.reserve_seconds + typical * 1.5)


class ResourceGuard:
    """Reads /proc/meminfo directly — no psutil dependency required. On
    non-Linux (local Windows dev) this always reports "plenty of memory
    available" so local runs are never artificially blocked."""

    LOW_MEMORY_LAUNCH_MB = 700   # below this, don't even launch the browser
    LOW_MEMORY_STOP_MB = 350     # below this mid-run, finish the current page and stop

    def mem_available_mb(self):
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return kb / 1024
        except Exception:
            pass
        return None  # unknown (e.g. not Linux) — callers treat None as "don't block"

    def ok_to_launch(self):
        mb = self.mem_available_mb()
        return mb is None or mb >= self.LOW_MEMORY_LAUNCH_MB

    def should_stop(self):
        mb = self.mem_available_mb()
        return mb is not None and mb < self.LOW_MEMORY_STOP_MB
