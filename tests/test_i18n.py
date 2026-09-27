from app.i18n import CATALOG, labels_for, translate


def test_both_languages_define_the_same_keys():
    assert set(CATALOG["ca"]) == set(CATALOG["es"])


def test_placeholders_are_the_same_in_both_languages():
    import re

    for key, text in CATALOG["ca"].items():
        assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", CATALOG["es"][key])), key


def test_translate_interpolates_and_falls_back():
    assert translate("es", "action.chase_supplier", reference="X-1").startswith("Reclamar al proveedor")
    assert "X-1" in translate("ca", "action.chase_supplier", reference="X-1")
    assert translate("fr", "status.mismatch") == "Discrepància"  # unknown language -> Catalan
    assert translate("es", "no.such.key") == "no.such.key"
    assert translate("ca", "action.chase_supplier") .count("{reference}") == 1  # missing param stays visible


def test_labels_for_returns_every_language():
    assert labels_for("export.h.status") == {"Resultat", "Resultado"}
