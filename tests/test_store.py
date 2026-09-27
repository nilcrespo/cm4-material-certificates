import json

from app.store import ConfirmedPairsStore


def test_confirm_and_check_is_order_independent(tmp_path):
    store_path = tmp_path / "confirmed_pairs.json"
    store_path.write_text("[]")
    store = ConfirmedPairsStore(store_path)

    assert store.is_confirmed("X5CrNi18-10", "X5CrNi18-10", "AISI 304") is False

    store.confirm("X5CrNi18-10", "X5CrNi18-10", "AISI 304")

    assert store.is_confirmed("X5CrNi18-10", "X5CrNi18-10", "AISI 304") is True
    assert store.is_confirmed("X5CrNi18-10", "AISI 304", "X5CrNi18-10") is True  # order shouldn't matter


def test_confirmation_persists_to_disk(tmp_path):
    store_path = tmp_path / "confirmed_pairs.json"
    store_path.write_text("[]")

    store_a = ConfirmedPairsStore(store_path)
    store_a.confirm("X5CrNi18-10", "X5CrNi18-10", "AISI 304")

    store_b = ConfirmedPairsStore(store_path)  # fresh instance, reload from disk
    assert store_b.is_confirmed("X5CrNi18-10", "X5CrNi18-10", "AISI 304") is True
    assert json.loads(store_path.read_text())  # file actually has content
