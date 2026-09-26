import json

import pytest

from app import config, db, reasoner

VALID_GROUPS = set(reasoner.GROUP_LABELS)


@pytest.mark.parametrize("category", config.CATEGORIES)
def test_checklist_files_are_well_formed(category):
    data = json.loads((config.CHECKLIST_DIR / f"{category}.json").read_text())
    codes = [i["code"] for i in data["items"]]
    assert len(codes) == len(set(codes))
    assert "99" in codes
    for item in data["items"]:
        assert item["group"] in VALID_GROUPS, item
        assert item["text"].strip()


def test_known_codes_present():
    with db.session() as conn:
        db.init_db()
        civil = {i["code"] for i in db.checklist(conn, "civil")}
        criminal = {i["code"] for i in db.checklist(conn, "criminal")}
        writ = {i["code"]: i for i in db.checklist(conn, "writ")}
    assert {"13(b)", "54", "86"} <= civil          # valuation, MACT deposit, synopsis
    assert {"49", "65", "75"} <= criminal          # compromise quashing, NDPS, synopsis
    assert writ["47(a)"]["cis_code"] == "114"      # alternative remedy statement
    assert writ["63"]["cis_code"] == "130"


def test_seed_is_idempotent():
    db.init_db()
    db.init_db()
    with db.session() as conn:
        n = conn.execute("SELECT COUNT(*) FROM checklist_items WHERE category='writ'").fetchone()[0]
    assert n == len(json.loads((config.CHECKLIST_DIR / "writ.json").read_text())["items"])


def test_checklist_prompt_block_mentions_cis_and_conditions():
    with db.session() as conn:
        db.init_db()
        blocks = reasoner.system_blocks("writ", db.checklist(conn, "writ"), [])
    text = blocks[1]["text"]
    assert "[CIS 114]" in text and "applies when" in text
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
