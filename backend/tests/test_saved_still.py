import asyncio
import time

import pytest

import main
import projects
import reference_jobs as jobs
from references import ReferenceError


@pytest.fixture(autouse=True)
def fresh_locks(monkeypatch):
    # asyncio locks bind to the first loop that contends them; give each test its own.
    monkeypatch.setattr(jobs, "start_lock", asyncio.Lock())
    monkeypatch.setattr(projects, "operation_lock", asyncio.Lock())
    monkeypatch.setattr(jobs, "check_quarantine", lambda: None)
    monkeypatch.setattr(jobs, "active", None)
    monkeypatch.setattr(jobs, "stopping", False)


def test_concurrent_previews_wait_their_turn_instead_of_conflicting():
    running, peak = [], []

    def generate(project_id, request):
        running.append(project_id)
        peak.append(len(running))
        time.sleep(0.05)
        running.remove(project_id)
        return project_id

    async def together():
        return await asyncio.gather(
            *(main.saved_still(name, None, generate) for name in ("status", "frame", "ocr"))
        )

    assert asyncio.run(together()) == ["status", "frame", "ocr"]
    assert max(peak) == 1  # still one at a time


def test_a_running_media_job_still_returns_reference_busy(monkeypatch):
    monkeypatch.setattr(jobs, "active", ("project", "operation", None, None))
    with pytest.raises(ReferenceError) as caught:
        asyncio.run(main.saved_still("project", None, lambda *args: "unused"))
    assert caught.value.code == "reference_busy"


def test_a_job_that_starts_while_a_preview_waits_wins(monkeypatch):
    async def scenario():
        await jobs.start_lock.acquire()
        waiting = asyncio.create_task(main.saved_still("project", None, lambda *args: "unused"))
        await asyncio.sleep(0.01)
        monkeypatch.setattr(jobs, "active", ("project", "operation", None, None))
        jobs.start_lock.release()
        return await asyncio.gather(waiting, return_exceptions=True)

    (result,) = asyncio.run(scenario())
    assert isinstance(result, ReferenceError) and result.code == "reference_busy"
