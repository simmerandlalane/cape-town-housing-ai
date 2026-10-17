"""Smoke test for the Streamlit app. Offline: it never presses Research, so no searches run."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_app_renders_questionnaire_without_errors():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any(b.label == "Research areas" for b in at.sidebar.button)
    assert "Research areas" in at.info[0].value  # empty state until research runs
    assert [h.value for h in at.sidebar.subheader][:2] == ["1. Your commute", "2. Budget"]
