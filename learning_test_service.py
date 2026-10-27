"""Read-only, manager-only AI lesson comparisons. No production gate overrides."""
import copy
import re
import threading
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

import ai_service
from learning_models import LearningCandidate, LearningKnowledgeVersion, LearningSettings
from learning_policy import DEFAULTS, content_hash, prohibited, sanitize
from learning_runtime import select_examples
from learning_service import privacy_ready
from models import BotConfiguration, BotKnowledgeEntry
from settings import settings
from shopify_catalog_service import get_product_by_handle

SLOTS = threading.BoundedSemaphore(2)
_lock = threading.Lock()
_runs = {}


class LessonTestError(Exception):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


class LessonTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidate_id: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=2000)
    mode: Literal["matched", "forced"] = "matched"
    context_kind: Literal["none", "quoted_product", "image_description", "ad_description"] = "none"
    context_text: str = Field(default="", max_length=1000)
    product_handle: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_context(self):
        if not self.message.strip():
            raise ValueError("Enter a sample question")
        if (self.context_kind == "none") != (not self.context_text.strip()):
            raise ValueError("Provide a synthetic description for the selected context type")
        if self.product_handle and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", self.product_handle):
            raise ValueError("Provide a Shopify handle, not a URL or product ID")
        return self


def admit(actor):
    """Process-local bounds, separate from automated worker-run budgets."""
    now = monotonic()
    with _lock:
        for key in list(_runs):
            _runs[key] = [when for when in _runs[key] if now - when < 3600]
            if not _runs[key]:
                del _runs[key]
        if len(_runs.get(actor, [])) >= 10 or (actor not in _runs and len(_runs) >= 128):
            raise LessonTestError("Lesson test limit reached. Maximum 10 comparisons per staff member per hour per web process.", 429)
        _runs.setdefault(actor, []).append(now)


def current_lesson(db, cid):
    candidate = db.get(LearningCandidate, cid)
    entry = db.get(BotKnowledgeEntry, candidate.knowledge_entry_id) if candidate and candidate.knowledge_entry_id else None
    version = (db.query(LearningKnowledgeVersion).filter_by(knowledge_item_id=entry.id)
        .order_by(LearningKnowledgeVersion.id.desc()).first()) if entry else None
    if (not candidate or candidate.publication_status != "PUBLISHED" or not entry or not entry.enabled or entry.archived
            or not version or version.candidate_id != cid or version.publication_status != "PUBLISHED"
            or version.content_hash != content_hash(entry.content, candidate.language, candidate.product_handle) or prohibited(entry.content)):
        raise LessonTestError("Select a currently published, enabled lesson. Draft, disabled, withdrawn or superseded lessons cannot be tested.")
    row = db.get(LearningSettings, 1)
    if row is None or not privacy_ready({**DEFAULTS, **row.config}):
        raise LessonTestError("Complete the existing privacy and AI-provider reviews before testing.")
    return candidate, entry, version


def compare(db, actor, values):
    candidate, entry, version = current_lesson(db, values.candidate_id)
    if not settings.OPENAI_API_KEY:
        raise LessonTestError("AI provider is not configured. No test requests were sent.")
    if not SLOTS.acquire(blocking=False):
        raise LessonTestError("Lesson testing is busy. Try again shortly.", 429)
    try:
        admit(actor)
        catalog = None
        if values.product_handle:
            product = get_product_by_handle(values.product_handle)
            if not product or product.get("handle") != values.product_handle:
                raise LessonTestError("The Shopify product could not be verified. No AI test was run.")
            catalog = [product]
        bot = db.query(BotConfiguration).first()
        snapshot = copy.deepcopy(bot.published_snapshot or {}) if bot else {}
        message = sanitize(values.message)
        if not message.strip():
            raise LessonTestError("Enter a non-sensitive synthetic sample question.")
        rows = [(entry, version)]
        if entry.topic == "product" and (entry.scope != "PRODUCT" or not entry.scope_reference):
            raise LessonTestError("This lesson is classified as product knowledge but has no linked product scope. A handle in this test cannot fix that. For general communication guidance, revise the lesson without product facts, save and revalidate, then approve and publish again. Do not use an unrelated product handle.")
        forced = select_examples(message, catalog, rows, snapshot, force_ids={entry.id})
        if forced is None:
            raise LessonTestError("This lesson needs matching, server-verified Shopify product context. Enter its product handle; force preview does not bypass product scope.")
        # Retain exactly the same authoritative data in both arms. Tests do not
        # pull arbitrary conversations or learning evidence into model prompts.
        matched = select_examples(message, catalog, rows, snapshot)
        included = values.mode == "forced" or matched is not None
        baseline = copy.deepcopy(forced)
        baseline.update(manager_reviewed_examples=[], automatic_communication_examples=[], versions=[])
        treatment = forced if included else copy.deepcopy(baseline)
        context = []
        if values.context_kind != "none":
            context = [{"direction": "inbound", "message_text":
                f"Synthetic {values.context_kind}; unverified description, not product authority: " + sanitize(values.context_text)}]
        preferences = {}
        if snapshot.get("global"):
            from bot_configuration_service import simulation_preferences
            try: preferences = simulation_preferences(snapshot)
            except ValueError as error: raise LessonTestError(str(error)) from None
        before = ai_service.ReplyTestContext(learned_payload=baseline, business_preferences=preferences)
        after = ai_service.ReplyTestContext(learned_payload=treatment, business_preferences=preferences)
        before_text = ai_service.generate_customer_reply(message, recent_context=context,
            catalog_results=catalog, test_context=before)
        # Recheck current publication after a provider wait. No evaluations,
        # publication, feedback, candidate or conversation rows are written.
        db.expire_all()
        _, _, still_current = current_lesson(db, values.candidate_id)
        if still_current.id != version.id:
            raise LessonTestError("The lesson changed during testing. Run a fresh test.")
        after_text = ai_service.generate_customer_reply(message, recent_context=context,
            catalog_results=catalog, test_context=after)
        db.expire_all()
        _, _, final = current_lesson(db, values.candidate_id)
        if final.id != version.id:
            raise LessonTestError("The lesson changed during testing. Run a fresh test.")
        def result(text, trace):
            return {"reply": sanitize(text) if trace.status == "GENERATED" else None,
                    "status": trace.status, "tokens": trace.total_tokens}
        return {"candidate_id": values.candidate_id, "version_id": version.id,
            "version_number": version.version_number, "lesson_title": entry.title,
            "mode": values.mode, "natural_match": matched is not None, "lesson_supplied": included,
            "reason": "FORCED_PREVIEW" if values.mode == "forced" else "MATCHED" if included else "NO_KEYWORD_MATCH",
            "without_lesson": result(before_text, before), "with_lesson": result(after_text, after),
            "model": settings.OPENAI_MODEL, "context_kind": values.context_kind,
            "input_redacted": message != values.message or sanitize(values.context_text) != values.context_text,
            "verified_shopify_context": bool(catalog), "live_retrieval_enabled": settings.LEARNING_RETRIEVAL_ENABLED,
            "notice": "AI generation comparison only, not the full WhatsApp routing pipeline. Supplied means included in the model request, not proof the model followed it. Inputs and displayed replies are privacy-filtered. Synthetic descriptions do not perform image analysis or prove ad/product identity. Two provider requests may incur API charges; no WhatsApp messages or learning records are created."}
    finally:
        SLOTS.release()
