import os
import tempfile

# Point the app at a throwaway data dir BEFORE any app import.
os.environ["INFRA_COPILOT_DATA_DIR"] = tempfile.mkdtemp(prefix="infra-copilot-test-")

import pytest  # noqa: E402

from app.db.database import init_db, session_factory  # noqa: E402
from app.db.models import ChatSession  # noqa: E402


@pytest.fixture()
async def db_ready():
    await init_db()
    yield


@pytest.fixture()
async def session_id(db_ready):
    async with session_factory() as db:
        s = ChatSession(title="test")
        db.add(s)
        await db.commit()
        return s.id
