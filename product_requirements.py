"""Ground short shopping answers in the immediately preceding question.

Age and preference come only from customer text. A category is a conversational
topic, never proof that any product exists or is appropriate for that child.
"""
import re

TOPICS = {
    "water bottle": r"water\s*bottles?|wathura\s*bottles?|වතුර\s*බෝතල",
    "school bag": r"school\s*bags?|පාසල්\s*බෑග",
    "toy": r"\btoys?\b|සෙල්ලම්\s*බඩු",
    "gift": r"\bgifts?\b|තෑගි",
    "bicycle": r"\bbicycles?\b|\bbikes?\b|බයිසික",
}
AGE_QUESTION = r"\bage\b|how old|wayasa|wayase|වයස|අවුරුදු\s*කීය"
OTHER_DETAIL = r"\b(?:capacity|size|budget|colour|color|brand|ml|litres?|liters?|height)\b"


def requirements(message, context=()):
    known = {"category": None, "age_years": None, "preference": None, "current_answer": False}
    pending_age = False
    turns = list(context)[-8:] + [{"direction": "inbound", "message_text": message}]
    for index, item in enumerate(turns):
        text = str(item.get("message_text") or "").casefold().strip()
        category = next((name for name, pattern in TOPICS.items() if re.search(pattern, text)), None)
        if item.get("direction") != "inbound":
            pending_age = bool(re.search(AGE_QUESTION, text) and not re.search(OTHER_DETAIL, text))
            # Recover the topic of a pending question if the initial request has
            # scrolled out of the bounded history. Never recover age from AI prose.
            if not known["category"] and category and "?" in text:
                known["category"] = category
            continue
        if category and category != known["category"]:
            known.update(category=category, age_years=None, preference=None)
        preference = next((label for label, pattern in {
            "boy": r"\bboys?\b|\bputha\b|පුතා",
            "girl": r"\bgirls?\b|\bduwa\b|දුව",
            "unisex": r"\bunisex\b|\beither\b",
        }.items() if re.search(pattern, text)), None)
        if re.search(r"\b(?:not for|don't want|do not want)\b", text):
            preference = None
        if preference:
            if known["preference"] and known["preference"] != preference:
                known["age_years"] = None
            known["preference"] = preference
        explicit = re.search(r"(?<!\d)(\d{1,2})\s*[- ]?(?:years?(?:\s*old)?|yrs?|awurudu|අවුරුදු)(?!\w)", text)
        if not explicit:
            explicit = re.search(r"(?:\bage\b|wayasa|වයස|awurudu|අවුරුදු)\s*(?:eka\s*)?(\d{1,2})(?!\d)", text)
        bare = re.fullmatch(r"\d{1,2}", text) if pending_age else None
        age = int(explicit.group(1)) if explicit else int(bare.group()) if bare else None
        if age is not None and 0 <= age <= 99:
            known["age_years"] = age
        if index == len(turns) - 1:
            known["current_answer"] = age is not None or (preference is not None and len(text.split()) <= 3)
            if re.search(r"\b(?:stop|cancel|no need|not interested|no thanks|not now|epa)\b|එපා", text):
                known["current_answer"] = False
        pending_age = False
    known["ready_for_catalog"] = (known["category"] in {"water bottle", "school bag", "toy", "gift"}
        and known["age_years"] is not None and known["preference"] is not None)
    return known


def repeat_clarification(discovery, known):
    """A completed age/preference exchange must not loop on those same details."""
    return (discovery.action in {"clarify", "reply"} and known["ready_for_catalog"] and known["current_answer"]
            and not re.search(OTHER_DETAIL, discovery.reply.casefold()))
