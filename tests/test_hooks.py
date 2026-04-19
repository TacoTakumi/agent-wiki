import json
from pathlib import Path


def test_tmp_settings_fixture_creates_empty_settings(tmp_settings):
    assert tmp_settings.exists()
    assert json.loads(tmp_settings.read_text()) == {}
