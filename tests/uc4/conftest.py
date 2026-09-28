import json

import pytest

from uc4_agent.werkzeuge import Werkzeugkasten


@pytest.fixture
def kasten(tmp_path):
    return Werkzeugkasten(run_id="test", runs_dir=tmp_path)


def lese_jsonl(pfad):
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]
