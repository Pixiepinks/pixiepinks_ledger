"""Strict read/input shapes verified from the owner-supplied 2026-10 export.

Shape verification is independent of update/replacement semantics. No environment
variable can approve those semantics. Owner-confirmed official replacement/untyped semantics and the completed P1
live pilot establish support; runtime flags and per-job release fencing still
default to deny writes. See docs/retail-barcode-production-release.md.
"""
MUTATION_SEMANTICS_VERIFIED=True
RELEASE_ID='20261119_standard_v1'
TYPES={'UPC','EAN','ISBN','GTIN','ASIN','NS_PID'}


def signature(value):
    if not isinstance(value,dict):return '?'
    kind=value.get('kind')
    if kind=='NON_NULL':return signature(value.get('ofType'))+'!'
    if kind=='LIST':return '['+signature(value.get('ofType'))+']'
    return value.get('name') or '?'


def fields(value,key):
    values=value.get(key) if isinstance(value,dict) else None
    if not isinstance(values,list):return {}
    return {v['name']:v for v in values if isinstance(v,dict) and isinstance(v.get('name'),str)}


def require_field(values,name,expected):
    return name in values and signature(values[name].get('type'))==expected


def shape_verified(data):
    variant=fields(data.get('variant'),'fields');bulk=fields(data.get('input'),'inputFields')
    barcode=fields(data.get('barcodeInput'),'inputFields')
    connection=fields(data.get('barcodeConnection'),'fields');node=fields(data.get('barcodeValue'),'fields')
    page=fields(data.get('barcodePageInfo'),'fields')
    mutation=fields((data.get('mutationSchema') or {}).get('mutationType'),'fields')
    update=mutation.get('productVariantsBulkUpdate',{});args=fields(update,'args')
    enum=(data.get('barcodeType') or {}).get('enumValues')
    if not isinstance(enum,list) or {v.get('name') for v in enum if isinstance(v,dict)}!=TYPES:return False
    checks=[(variant,'barcodes','ProductVariantBarcodeConnection!'),(variant,'barcode','String'),
        (bulk,'id','ID'),(bulk,'barcodes','[BarcodeInput!]'),(barcode,'value','String!'),(barcode,'type','BarcodeType'),
        (connection,'nodes','[ProductVariantBarcode!]!'),(connection,'pageInfo','PageInfo!'),
        (node,'value','String!'),(node,'type','BarcodeType'),(page,'hasNextPage','Boolean!'),(page,'endCursor','String'),
        (args,'productId','ID!'),(args,'variants','[ProductVariantsBulkInput!]!'),(args,'allowPartialUpdates','Boolean')]
    if not all(require_field(v,k,s) for v,k,s in checks):return False
    if set(barcode)!={'value','type'} or 'defaultValue' not in barcode['type'] or barcode['type']['defaultValue'] is not None:return False
    if signature(update.get('type'))!='ProductVariantsBulkUpdatePayload':return False
    # Omitted unrelated fields/arguments must not be newly required without defaults.
    for values,allowed in [(bulk,{'id','barcodes'}),(barcode,{'value','type'}),(args,{'productId','variants','allowPartialUpdates'})]:
        if any(signature(v.get('type')).endswith('!') or v.get('defaultValue') is not None for k,v in values.items() if k not in allowed):return False
    pagination=fields(variant['barcodes'],'args')
    return require_field(pagination,'first','Int') and require_field(pagination,'after','String')
