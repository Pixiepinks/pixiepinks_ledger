"""Read-only Inbox metadata; learning evidence and consent references stay private."""
from learning_models import LearningSource, LearningAnalysis, LearningJob
from learning_service import configuration, learning_enabled
from phone_country_service import phone_country


def conversation_details(db, conversation):
    source = db.query(LearningSource).filter_by(conversation_id=conversation.id).one_or_none()
    config = configuration(db)
    consented = bool(source and source.allowed and source.consent_at and source.consent_reference)
    if source and source.withdrawn_at:
        eligibility = 'WITHDRAWN'
    elif not consented:
        eligibility = 'EXCLUDED'
    elif source.evaluation_only:
        eligibility = 'EVALUATION_ONLY'
    else:
        eligibility = 'ELIGIBLE'
    latest = None
    if source:
        latest = (db.query(LearningAnalysis.status, LearningAnalysis.completed_at, LearningJob.status.label('job_status'))
                  .outerjoin(LearningJob, LearningJob.id == LearningAnalysis.job_id)
                  .filter(LearningAnalysis.source_id == source.id)
                  .order_by(LearningAnalysis.created_at.desc(), LearningAnalysis.id.desc()).first())
    return {
        'id': conversation.id,
        'customer_name': conversation.customer_name or 'WhatsApp customer',
        'phone_number': conversation.phone_number,
        'phone_country': phone_country(conversation.phone_number),
        'mode': conversation.mode,
        # There is no assignment column/relation on WhatsAppConversation. A
        # historical takeover is not represented as a current staff assignment.
        'assigned_staff': None,
        'taken_over_by': conversation.taken_over_by,
        'learning': {
            'eligibility': eligibility,
            'engine_enabled': bool(learning_enabled(config)),
            'analysis_status': latest.status if latest else None,
            'job_status': latest.job_status if latest else None,
            'analysis_completed_at': (latest.completed_at.isoformat() + 'Z' if latest and latest.status == "COMPLETE" and latest.completed_at else None),
        },
    }
