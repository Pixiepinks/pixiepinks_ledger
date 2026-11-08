"""Conversation-scoped delivery collection after a product/service decision.

Each prompt has its own durable send claim. Customer data stays in the retained
run, and a confirmation is an order request for staff, never a payment receipt.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import re
import secrets
from models import CustomerQuestionProductSend as Item, WhatsAppConversationMessage as Message
from whatsapp_service import send_whatsapp_text

FIELDS = ('name', 'address', 'phone1', 'phone2')
PROMPTS = {
    'name': 'ඇණවුම සඳහා ඔබගේ සම්පූර්ණ නම එවන්න.',
    'address': 'බයිසිකලය එවිය යුතු සම්පූර්ණ ලිපිනය එවන්න. නිවසේ අංකය, වීදිය, ප්‍රදේශය සහ නගරය ඇතුළත් කරන්න.',
    'phone1': 'ඔබව සම්බන්ධ කරගත හැකි ප්‍රධාන දුරකථන අංකය එවන්න. උදා: 0771234567 හෝ +94771234567',
    'phone2': 'දෙවන දුරකථන අංකයක් තිබේ නම් එවන්න. නැත්නම් පහත Skip බොත්තම තෝරන්න.',
}
ERRORS = {
    'name': 'කරුණාකර අකුරු 2–100 අතර සම්පූර්ණ නමක් එවන්න.',
    'address': 'කරුණාකර අකුරු 10–350 අතර සම්පූර්ණ ලිපිනයක් එවන්න.',
    'phone1': 'වලංගු දුරකථන අංකයක් එවන්න. උදා: 0771234567 හෝ +94771234567',
    'phone2': 'වලංගු දෙවන දුරකථන අංකයක් එවන්න, නැත්නම් Skip තෝරන්න.',
}


def selected(run):
    if run.step >= len(run.definition['steps']):
        return None
    step = run.definition['steps'][run.step]
    answer = run.answers.get(step['key'])
    return answer if step.get('service', {}).get('collect_delivery') and isinstance(answer, dict) and answer.get('greasing_service') else None


def pending(run):
    answer = selected(run)
    return bool(answer and answer.get('delivery_details', {}).get('status') in {'COLLECTING', 'REVIEW'})


def save(run, details):
    key = run.definition['steps'][run.step]['key']
    run.answers = {**run.answers, key: {**run.answers[key], 'delivery_details': details}}


def begin(run):
    save(run, {'stage': 'name', 'sequence': 0, 'status': 'COLLECTING'})
    run.status = 'QUEUED'
    run.provider_id = None
    run.token = secrets.token_urlsafe(24)


def position(run):
    return -4 - selected(run)['delivery_details']['sequence']


def claim(db, run):
    item = db.query(Item).filter_by(run_id=run.id, step=run.step, position=position(run)).one_or_none()
    if item is None:
        db.add(Item(run_id=run.id, step=run.step, position=position(run), status='SENDING'))
        return True
    if item.status == 'QUEUED':
        item.status = 'SENDING'
        return True
    run.status = 'UNCERTAIN'
    run.version += 1
    return False


def summary(answer):
    details = answer['delivery_details']
    service = answer['greasing_service']
    return (
        'ඔබගේ ඇණවුමේ විස්තර පරීක්ෂා කරන්න 💖\n'
        f"භාණ්ඩය: {answer['title'][:100]} · {answer.get('variant_title', '')[:60]}\n"
        'ප්‍රමාණය: 1\n'
        f"බයිසිකලයේ මිල: Rs. {Decimal(answer['price']):,.2f}\n"
        f"ග්‍රීස් සේවාව: {'ඔව්' if service['accepted'] else 'නැහැ'} · Rs. {Decimal(service['charge']):,.2f}\n"
        'Delivery: FREE\n'
        f"මුළු මුදල: Rs. {Decimal(service['quoted_total']):,.2f}\n\n"
        f"නම: {details['name']}\nලිපිනය: {details['address']}\n"
        f"Phone 1: {details['phone1']}\nPhone 2: {details.get('phone2') or '—'}\n\n"
        'විස්තර නිවැරදි නම් Confirm order තෝරන්න. වෙනස් කිරීමට Edit details තෝරන්න.'
    )


def send_claimed(db, run, conversation, item):
    from customer_questions import audit, send_buttons
    answer = selected(run)
    details = answer['delivery_details']
    stage = details['stage']
    body = summary(answer) if stage == 'review' else ((ERRORS[stage] + '\n\n') if details.get('invalid') else '') + PROMPTS[stage]
    labels = ['Confirm order', 'Edit details'] if stage == 'review' else ['Skip'] if stage == 'phone2' else []
    options = [{'id': f'ppq:{run.token}:{run.step}:{i}', 'title': label} for i, label in enumerate(labels)]
    try:
        provider = (send_buttons(conversation.phone_number, body, options) if labels else
                    send_whatsapp_text(conversation.phone_number, body, return_message_id=True))
    except Exception:
        provider = None
    if not isinstance(provider, str) or not 1 <= len(provider) <= 255:
        item.status = 'UNCERTAIN'
        item.reason = 'Delivery-details prompt delivery is uncertain. Check chat; no automatic retry.'
        run.status = 'UNCERTAIN'
    else:
        item.status = 'SENT'
        item.provider_id = provider
        run.provider_id = provider
        run.status = 'WAITING'
        db.add(Message(phone_number=conversation.phone_number, direction='outbound', message_text=body,
            whatsapp_message_id=provider, response_kind='customer_question', send_status='sent', analytics_actor_kind='automatic'))
        conversation.last_message_at = max(conversation.last_message_at or datetime.utcnow(), datetime.utcnow())
    run.updated_at = datetime.utcnow()
    run.version += 1
    audit(db, run, 'SENT' if item.status == 'SENT' else 'UNCERTAIN', 'System',
          format='delivery_details', field=stage, options=labels, provider_id=item.provider_id)


def valid_text(stage, raw):
    text = raw.strip()
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        return None
    if stage in {'name', 'address'}:
        text = ' '.join(text.split())
        minimum, maximum = (2, 100) if stage == 'name' else (10, 350)
        return text if minimum <= len(text) <= maximum and any(c.isalpha() for c in text) else None
    if stage == 'phone2' and text.casefold() in {'skip', 'none', 'no', 'නැහැ'}:
        return ''
    if not re.fullmatch(r'\+?[0-9 ()-]+', text):
        return None
    phone = re.sub(r'[ ()-]', '', text)
    if re.fullmatch(r'0[1-9][0-9]{8}', phone):
        return '+94' + phone[1:]
    if re.fullmatch(r'94[1-9][0-9]{8}', phone):
        return '+' + phone
    return phone if re.fullmatch(r'\+[1-9][0-9]{7,14}', phone) else None


def sent_prompt(db, run, incoming):
    item = db.query(Item).filter_by(run_id=run.id, step=run.step, position=position(run), status='SENT').one_or_none()
    return bool(item and item.provider_id == run.provider_id and
        item.created_at.replace(microsecond=0) <= incoming.created_at <= datetime.utcnow() + timedelta(minutes=5) and
        (not incoming.reply_to_whatsapp_message_id or incoming.reply_to_whatsapp_message_id == run.provider_id))


def advance(db, run, incoming, value=None, choice=None):
    from customer_questions import audit
    details = dict(selected(run)['delivery_details'])
    stage = details['stage']
    if stage == 'review':
        if choice == 0:
            details.update(status='CONFIRMED', confirmed_at=datetime.utcnow().isoformat() + 'Z', confirmation_message_id=incoming.id)
            save(run, details)
            run.step = len(run.definition['steps'])
            run.status = 'COMPLETE'
            run.version += 1
            run.updated_at = datetime.utcnow()
            audit(db, run, 'DELIVERY_CONFIRMED', 'Customer', inbound_id=incoming.id)
            audit(db, run, 'COMPLETE', 'System', note='Customer confirmed delivery details and quote. Staff can prepare the invoice; payment is not verified.')
            return
        # Re-enter all fields without altering the product or service decision.
        details = {'stage': 'name', 'status': 'COLLECTING', 'sequence': details['sequence']}
        audit(db, run, 'DELIVERY_EDIT', 'Customer', inbound_id=incoming.id)
    elif value is None:
        details['invalid'] = True
        audit(db, run, 'DELIVERY_INVALID', 'Customer', field=stage, inbound_id=incoming.id)
    else:
        details[stage] = value
        details.pop('invalid', None)
        details['stage'] = FIELDS[FIELDS.index(stage) + 1] if stage != 'phone2' else 'review'
        details['status'] = 'REVIEW' if details['stage'] == 'review' else 'COLLECTING'
        audit(db, run, 'DELIVERY_FIELD', 'Customer', field=stage, inbound_id=incoming.id)
    details['sequence'] += 1
    save(run, details)
    run.status = 'QUEUED' if details['sequence'] < 100 else 'UNCERTAIN'
    run.provider_id = None
    run.token = secrets.token_urlsafe(24)
    run.version += 1
    run.updated_at = datetime.utcnow()


def receive_text(db, conversation, incoming, *, verified=True):
    """Return True while this run owns delivery text, including paused/OFF states.

    Suppress AI/consent interpretation even for ignored, stale or invalid fields.
    """
    from customer_questions import config, latest
    control = config(db, True, optional=True)
    if control is None:
        return False
    run = latest(db, conversation.id, True)
    if not run or run.status in {'STOPPED', 'COMPLETE'} or not pending(run):
        return False
    if not verified or not control.enabled or run.status != 'WAITING' or not sent_prompt(db, run, incoming):
        return True
    stage = selected(run)['delivery_details']['stage']
    if stage != 'review':
        advance(db, run, incoming, valid_text(stage, incoming.message_text or ''))
    return True


def receive_button(db, run, incoming, choice):
    if incoming.response_kind != 'question_reply' or not sent_prompt(db, run, incoming):
        return
    stage = selected(run)['delivery_details']['stage']
    if stage == 'phone2' and choice == 0:
        advance(db, run, incoming, '')
    elif stage == 'review' and choice in {0, 1}:
        advance(db, run, incoming, choice=choice)
