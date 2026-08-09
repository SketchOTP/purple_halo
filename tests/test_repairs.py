import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ph_cli import parse_every, set_schedule_config


def test_interval_rejects_zero():
    try:
        parse_every("0h")
    except SystemExit:
        return
    raise AssertionError("zero interval accepted")


def test_specific_time_rejects_invalid():
    try:
        set_schedule_config(kind="times", runs=[{"at": "not-a-time"}])
    except SystemExit:
        return
    raise AssertionError("invalid time accepted")


def test_installer_has_no_destructive_sync():
    text = (ROOT / "scripts/install_to_repo.sh").read_text()
    assert "rsync -a --delete" not in text
    assert not re.search(r"rm -rf \\\"\$dst\\\"", text)


def test_paragon_defaults():
    text = (ROOT / "scripts/paragon_client.py").read_text()
    assert "atlas-2.tail1a5964.ts.net:10000/v1" in text
    assert 'PARAGON_MODEL", "paragon"' in text


def test_api_gets_do_not_provision_authentication_secret():
    text = (ROOT / "scripts/operator_api.py").read_text()
    assert "Set-Cookie" not in text
    assert "ph_session={_api_token()}" not in text
