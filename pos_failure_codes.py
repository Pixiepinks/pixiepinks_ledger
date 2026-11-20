"""Allowlisted POS failure metadata; never persist raw exceptions/SQL/payloads."""
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

STAGES={'SHOPIFY_SEARCH','SHOPIFY_READBACK','ORDER_VERIFICATION','LOCAL_POSTING','LOCAL_FLUSH','REFUND_VERIFICATION'}
VERIFICATION_CODES={
 'Exactly one authoritative matching order required; zero matches never permit a blind retry':'ORDER_MATCH_COUNT_NOT_ONE',
 'Order identity/correlation mismatch':'ORDER_IDENTITY_MISMATCH',
 'Customer association mismatch':'CUSTOMER_ASSOCIATION_MISMATCH',
 'Order currency mismatch':'ORDER_CURRENCY_MISMATCH',
 'Order financial outcome not verified':'ORDER_PAYMENT_OR_TOTAL_MISMATCH',
 'Tax or Shopify-native discount outcome mismatch':'ORDER_TAX_OR_DISCOUNT_MISMATCH',
 'Order lines incomplete':'ORDER_LINES_INCOMPLETE',
 'Order line mismatch':'ORDER_LINE_MISMATCH',
 'Order lines mismatch':'ORDER_SEGMENTS_MISMATCH',
 'Fulfillment unavailable':'FULFILLMENT_UNAVAILABLE',
 'Store fulfillment not verified':'FULFILLMENT_STATUS_OR_LOCATION_MISMATCH',
 'Fulfillment lines incomplete':'FULFILLMENT_LINES_INCOMPLETE',
 'Fulfillment line mismatch':'FULFILLMENT_LINE_MISMATCH',
 'Fulfillment quantity mismatch':'FULFILLMENT_QUANTITY_MISMATCH',
 'Payment transaction coverage incomplete':'PAYMENT_COVERAGE_INCOMPLETE',
 'Manual payment evidence mismatch':'PAYMENT_EVIDENCE_MISMATCH',
 'Use a nonnegative LKR amount with at most two decimals':'INVALID_REMOTE_MONEY',
}
CODES=set(VERIFICATION_CODES.values())|{'DATABASE_ERROR','POSTING_UNBALANCED','SHOPIFY_READBACK_UNAVAILABLE','SHOPIFY_SEARCH_UNAVAILABLE','MALFORMED_READBACK','INTERNAL_FAILURE'}


def failure_code(stage,error):
    if isinstance(error,SQLAlchemyError):return 'DATABASE_ERROR'
    message=error.detail if isinstance(error,HTTPException) else str(error)
    if isinstance(message,str) and message in VERIFICATION_CODES:return VERIFICATION_CODES[message]
    if isinstance(error,HTTPException) and message=='Unbalanced POS posting blocked':return 'POSTING_UNBALANCED'
    if stage=='SHOPIFY_SEARCH':return 'SHOPIFY_SEARCH_UNAVAILABLE'
    if stage=='SHOPIFY_READBACK':return 'SHOPIFY_READBACK_UNAVAILABLE'
    if stage in {'ORDER_VERIFICATION','REFUND_VERIFICATION'} and isinstance(error,(KeyError,TypeError,ValueError)):return 'MALFORMED_READBACK'
    return 'INTERNAL_FAILURE'


def sanitized(detail):
    if not isinstance(detail,dict) or detail.get('stage') not in STAGES or detail.get('failure_code') not in CODES:return None
    return {'stage':detail['stage'],'failure_code':detail['failure_code']}
