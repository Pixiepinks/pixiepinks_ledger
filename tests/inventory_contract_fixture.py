"""Official 2026-10 type fixtures; production diagnostic independently verifies the live schema."""
def typ(value):
    if value.endswith('!'):return {'kind':'NON_NULL','ofType':typ(value[:-1])}
    if value.startswith('['):return {'kind':'LIST','ofType':typ(value[1:-1])}
    return {'kind':'SCALAR','name':value}


def schema():
    def inputs(values):return {'inputFields':[{'name':name,'type':typ(value),'defaultValue':None} for name,value in values.items()]}
    def fields(values):return {'fields':[{'name':name,'type':typ(value),'args':[]} for name,value in values.items()]}
    mutations=fields({'inventorySetQuantities':'InventorySetQuantitiesPayload','inventoryAdjustQuantities':'InventoryAdjustQuantitiesPayload'})
    for field,kind in zip(mutations['fields'],['InventorySetQuantitiesInput!','InventoryAdjustQuantitiesInput!']):field['args']=[{'name':'input','type':typ(kind),'defaultValue':None}]
    group=fields({'id':'ID!','createdAt':'DateTime!','reason':'String!','referenceDocumentUri':'String','changes':'[InventoryChange!]!'})
    next(f for f in group['fields'] if f['name']=='changes')['args']=[{'name':'quantityNames','type':typ('[String!]'),'defaultValue':None}]
    return {'currentAppInstallation':{'accessScopes':[{'handle':s} for s in ['read_inventory','write_inventory','read_locations','read_products']]},
        'set':inputs({'name':'String!','reason':'String!','referenceDocumentUri':'String','quantities':'[InventoryQuantityInput!]!'}),
        'quantity':inputs({'inventoryItemId':'ID!','locationId':'ID!','quantity':'Int!','changeFromQuantity':'Int'}),
        'adjust':inputs({'name':'String!','reason':'String!','referenceDocumentUri':'String','changes':'[InventoryChangeInput!]!'}),
        'change':inputs({'inventoryItemId':'ID!','locationId':'ID!','delta':'Int!','changeFromQuantity':'Int','ledgerDocumentUri':'String'}),'mutation':mutations,
        'setPayload':fields({'inventoryAdjustmentGroup':'InventoryAdjustmentGroup','userErrors':'[InventorySetQuantitiesUserError!]!'}),
        'adjustPayload':fields({'inventoryAdjustmentGroup':'InventoryAdjustmentGroup','userErrors':'[InventoryAdjustQuantitiesUserError!]!'}),
        'group':group,
        'setError':fields({'field':'[String!]','message':'String!','code':'InventorySetQuantitiesUserErrorCode'}),
        'adjustError':fields({'field':'[String!]','message':'String!','code':'InventoryAdjustQuantitiesUserErrorCode'}),
        'outputChange':fields({'name':'String!','delta':'Int!','quantityAfterChange':'Int','ledgerDocumentUri':'String','item':'InventoryItem','location':'Location'}),
        '__schema':{'directives':[{'name':'idempotent','locations':['FIELD'],'args':[{'name':'key','type':typ('String!'),'defaultValue':None}]}]}}
