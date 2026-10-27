from copy import deepcopy

from html.parser import HTMLParser


class Page(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.text, self.summary = [], {}
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "change_summary":
            self.summary = attrs

    def handle_data(self, value):
        if value.strip(): self.text.append(value.strip())

    def content(self):
        return " ".join(self.text)

from bot_configuration_service import default_snapshot, draft_change_summary, ensure_configuration, invalidate_cache, save_draft
from models import BotConfigurationVersion
from test_whatsapp_quick_messages import inbox


def test_automatic_summary_describes_changes_without_copying_instruction_contents():
    before = default_snapshot(); after = deepcopy(before)
    after["global"].update(instructions="PRIVATE BUSINESS INSTRUCTION", maximum_products=10,
                           automatic_product_attachments=True)
    summary = draft_change_summary(before, after)
    assert "Global instructions updated" in summary and "3 → 10" in summary
    assert "Automatic product attachments: enabled" in summary
    assert "PRIVATE BUSINESS INSTRUCTION" not in summary
    assert draft_change_summary(before, before) == "Republish current configuration"
    old = deepcopy(before); del old["global"]["automatic_product_attachments"]
    assert draft_change_summary(old, before) == "Republish current configuration"


def test_summary_prefill_actual_live_flags_and_blank_backend_fallback(inbox):
    client, sessions, _, _, _, _ = inbox
    invalidate_cache()
    try:
        with sessions() as db:
            config = ensure_configuration(db)
            draft = deepcopy(config.draft_snapshot)
            draft["global"].update(maximum_products=10, automatic_product_attachments=True)
            save_draft(db, draft, "test", "Edited draft")
        response = client.get("/crm/bot-management/versions")
        assert response.status_code == 200
        page = Page(response.text)
        summary = page.summary
        assert "Automatic product attachments: enabled" in summary["value"]
        assert "Automatic product attachments: OFF" in page.content()
        assert "required" not in summary
        blocked = client.post("/crm/bot-management/publish", data={"change_summary": ""}, follow_redirects=False)
        assert blocked.status_code == 400
        with sessions() as db:
            assert db.query(BotConfigurationVersion).count() == 1
        published = client.post("/crm/bot-management/publish", data={"confirm": "PUBLISH"}, follow_redirects=False)
        assert published.status_code == 303
        with sessions() as db:
            version = db.query(BotConfigurationVersion).filter_by(version=2).one()
            assert version.change_summary == summary["value"]
            assert version.snapshot["global"]["automatic_product_attachments"] is True
        live = Page(client.get("/crm/bot-management/versions").text).content()
        assert "Automatic product attachments: ON" in live
        assert "Maximum products: 10" in live
        result = client.post("/crm/bot-management/publish", data={"confirm": "PUBLISH", "change_summary": "My own summary"}, follow_redirects=False)
        assert result.status_code == 303
        with sessions() as db:
            assert db.query(BotConfigurationVersion).filter_by(version=3).one().change_summary == "My own summary"
    finally:
        invalidate_cache()
