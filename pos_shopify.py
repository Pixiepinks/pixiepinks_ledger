"""Dedicated explicit-version POS transport. Never adjusts inventory directly."""
import json,time,hashlib
from decimal import Decimal
import httpx
from retail_inventory import InventoryError,TYPE,signature,fields
from shopify_catalog_service import ShopifyCatalogClient
from settings import settings
import inventory_shopify

VERSION='2026-10'
CONTRACT='pos-order-create-2026-10-v1'
ORDER_FIELDS='''id name customer { id } sourceIdentifier tags displayFinancialStatus displayFulfillmentStatus cancelledAt
 totalPriceSet { shopMoney { amount currencyCode } }
 totalTaxSet { shopMoney { amount currencyCode } } totalDiscountsSet { shopMoney { amount currencyCode } }
 totalReceivedSet { shopMoney { amount currencyCode } }
 totalRefundedSet { shopMoney { amount currencyCode } }
 lineItems(first:60) { nodes { id quantity refundableQuantity variant { id } originalUnitPriceSet { shopMoney { amount currencyCode } } customAttributes { key value } } pageInfo { hasNextPage endCursor } }
 transactions(first:20) { id kind status gateway amountSet { shopMoney { amount currencyCode } } }
 fulfillments(first:2) { id status location { id } fulfillmentLineItems(first:60) { nodes { quantity lineItem { id } } pageInfo { hasNextPage endCursor } } }
 refunds { id note totalRefundedSet { shopMoney { amount currencyCode } }
 refundLineItems(first:60) {nodes {quantity restockType location {id} lineItem {id}} pageInfo {hasNextPage endCursor}}
 transactions(first:2) {nodes {id kind status gateway parentTransaction {id} amountSet {shopMoney {amount currencyCode}}} pageInfo {hasNextPage endCursor}}
 }
'''
READ='query POSOrder($id:ID!) { shop { myshopifyDomain currencyCode } order(id:$id) { '+ORDER_FIELDS+' } }'
FIND='query POSFind($query:String!,$after:String) { shop {myshopifyDomain currencyCode} orders(first:50,query:$query,after:$after) {nodes {id sourceIdentifier tags} pageInfo {hasNextPage endCursor}} }'
CREATE='mutation POSCreate($order:OrderCreateOrderInput!,$options:OrderCreateOptionsInput) { orderCreate(order:$order,options:$options) { order { id } userErrors {field message} } }'
REFUND='mutation POSRefund($input:RefundInput!,$key:String!) { refundCreate(input:$input) @idempotent(key:$key) { refund {id} userErrors {field message} } }'
CUSTOMER='query POSCustomer($id:ID!) { shop {myshopifyDomain currencyCode} customer(id:$id) {id} }'
PRODUCT='query POSProduct($id:ID!) { shop {myshopifyDomain currencyCode} productVariant(id:$id) {id price taxable inventoryItem {id tracked} product {id status} } }'
INPUTS=['OrderCreateOrderInput','OrderCreateOptionsInput','OrderCreateLineItemInput','OrderCreateFulfillmentInput','OrderCreateOrderTransactionInput','RefundInput','RefundLineItemInput','OrderTransactionInput','OrderCreateCustomerInput','OrderCreateAssociateCustomerAttributesInput']
PROBE='query POSContract { shop {myshopifyDomain currencyCode} currentAppInstallation { accessScopes {handle} } '+ ' '.join('t'+str(i)+':__type(name:"'+name+'") {inputFields {name type {'+TYPE+'} defaultValue}}' for i,name in enumerate(INPUTS))+' inventoryEnum:__type(name:"OrderCreateInputsInventoryBehavior") {enumValues {name}} restockEnum:__type(name:"RefundLineItemRestockType") {enumValues {name}} mutation:__type(name:"Mutation") {fields {name args {name type {'+TYPE+'}}}} __schema {directives {name locations args {name type {'+TYPE+'}}}} }'

class POSRemoteError(Exception):pass

class POSShopify(ShopifyCatalogClient):
    def request(self,query,variables):
        if query not in {READ,FIND,CREATE,REFUND,PRODUCT,PROBE,CUSTOMER}:raise POSRemoteError('Unsupported POS operation')
        if query==CREATE and not settings.POS_CHECKOUT_WRITE_ENABLED:raise POSRemoteError('POS checkout writes disabled')
        if query==REFUND and not settings.POS_REFUND_WRITE_ENABLED:raise POSRemoteError('POS refund writes disabled')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{VERSION}/graphql.json',headers={'X-Shopify-Access-Token':self.get_token()},json={'query':query,'variables':variables},timeout=httpx.Timeout(12,connect=3))
            if response.status_code!=200 or response.headers.get('X-Shopify-API-Version')!=VERSION:raise POSRemoteError('Shopify status/version unavailable')
            body=response.json()
            if not isinstance(body,dict) or body.get('errors') or not isinstance(body.get('data'),dict):raise POSRemoteError('Shopify POS response unavailable')
            data=body['data']
            if query not in {CREATE,REFUND} and ((data.get('shop') or {}).get('myshopifyDomain')!=self.base_url.removeprefix('https://') or (data.get('shop') or {}).get('currencyCode')!='LKR'):raise POSRemoteError('Shop/currency mismatch')
            return data
        except POSRemoteError:raise
        except Exception:raise POSRemoteError('Shopify POS outcome unavailable') from None

    def capabilities(self):
        data=self.request(PROBE,{})
        scopes={v['handle'] for v in (data.get('currentAppInstallation') or {}).get('accessScopes',[]) if isinstance(v,dict) and isinstance(v.get('handle'),str)}
        required={'OrderCreateOrderInput':{'lineItems':'[OrderCreateLineItemInput]','transactions':'[OrderCreateOrderTransactionInput]','fulfillment':'OrderCreateFulfillmentInput','sourceIdentifier':'String','currency':'CurrencyCode','tags':'[String]'},
            'OrderCreateOptionsInput':{'inventoryBehaviour':'OrderCreateInputsInventoryBehavior'},'OrderCreateLineItemInput':{'variantId':'ID','priceSet':'MoneyBagInput','quantity':'Int','taxLines':'[OrderCreateTaxLineInput]','properties':'[OrderCreateLineItemPropertyInput]'},
            'OrderCreateFulfillmentInput':{'locationId':'ID'},'OrderCreateOrderTransactionInput':{'amountSet':'MoneyBagInput','gateway':'String','kind':'OrderTransactionKind','status':'OrderTransactionStatus','locationId':'ID'},
            'RefundInput':{'orderId':'ID','refundLineItems':'[RefundLineItemInput]','transactions':'[OrderTransactionInput]'},'RefundLineItemInput':{'lineItemId':'ID','quantity':'Int','restockType':'RefundLineItemRestockType','locationId':'ID'},'OrderTransactionInput':{'amount':'Money','gateway':'String','kind':'OrderTransactionKind','parentId':'ID','orderId':'ID'},
            'OrderCreateCustomerInput':{'toAssociate':'OrderCreateAssociateCustomerAttributesInput'},'OrderCreateAssociateCustomerAttributesInput':{'id':'ID'}}
        checks={}
        for i,name in enumerate(INPUTS):
            rows=(data.get('t'+str(i)) or {}).get('inputFields',[]);values={r['name']:signature(r.get('type')) for r in rows}
            checks[name]=all(values.get(k)==v for k,v in required[name].items())
        mutations=fields(data.get('mutation'))
        def arguments(name,expected):
            args={a['name']:signature(a.get('type')) for a in (mutations.get(name) or {}).get('args',[])}
            return all(args.get(k)==v for k,v in expected.items())
        checks['mutations']=arguments('orderCreate',{'order':'OrderCreateOrderInput','options':'OrderCreateOptionsInput'}) and arguments('refundCreate',{'input':'RefundInput'})
        # Required additions cannot silently change the reviewed contract.
        supplied={'OrderCreateOrderInput':{'currency','sourceIdentifier','tags','note','lineItems','taxesIncluded','fulfillment','transactions','customer'},'OrderCreateOptionsInput':{'inventoryBehaviour','sendReceipt','sendFulfillmentReceipt'},'OrderCreateLineItemInput':{'variantId','quantity','priceSet','requiresShipping','taxable','properties','taxLines'},'OrderCreateFulfillmentInput':{'locationId','notifyCustomer'},'OrderCreateOrderTransactionInput':{'amountSet','gateway','kind','status','locationId'},'RefundInput':{'orderId','currency','note','notify','refundLineItems','transactions'},'RefundLineItemInput':{'lineItemId','quantity','restockType','locationId'},'OrderTransactionInput':{'orderId','parentId','gateway','kind','amount'},'OrderCreateCustomerInput':{'toAssociate'},'OrderCreateAssociateCustomerAttributesInput':{'id'}}
        for i,name in enumerate(INPUTS):
            rows=(data.get('t'+str(i)) or {}).get('inputFields',[])
            checks[name]=checks[name] and not any((v.get('type') or {}).get('kind')=='NON_NULL' and v.get('defaultValue') is None and v['name'] not in supplied[name] for v in rows)

        checks['inventory_policy']='DECREMENT_OBEYING_POLICY' in {v['name'] for v in (data.get('inventoryEnum') or {}).get('enumValues',[])}
        checks['restock_policy']={'RETURN','NO_RESTOCK'}<={v['name'] for v in (data.get('restockEnum') or {}).get('enumValues',[])}
        directive=next((d for d in (data.get('__schema') or {}).get('directives',[]) if d.get('name')=='idempotent'),{})
        checks['refund_idempotent']='FIELD' in directive.get('locations',[]) and any(a.get('name')=='key' and signature(a.get('type'))=='String' for a in directive.get('args',[]))
        self.read('gid://shopify/Order/0');self.find('POS-READ-ONLY-SCHEMA-CHECK')
        return {'version':VERSION,'scopes':sorted(scopes),'checks':checks,'compatible':all(checks.values()),
            'checkout_ready':all(checks.values()) and {'read_products','read_orders','write_orders','read_inventory','read_locations'}<=scopes,
            'refund_ready':all(checks.values()) and {'read_orders','write_orders'}<=scopes}

    def inspect(self,line):
        data=self.request(PRODUCT,{'id':line['variant_id']});r=data.get('productVariant')
        if not isinstance(r,dict) or r.get('id')!=line['variant_id'] or (r.get('product') or {}).get('id')!=line['product_id'] or r['product'].get('status')!='ACTIVE' or (r.get('inventoryItem') or {}).get('id')!=line['inventory_item_id'] or r['inventoryItem'].get('tracked') is not True or type(r.get('taxable')) is not bool:raise POSRemoteError('Product identity/tracking unavailable')
        stock=inventory_shopify.client.observe(line)
        return {'price':str(r['price']),'taxable':r['taxable'],'available':stock['available'],'on_hand':stock['on_hand'],'observed_at':time.time()}

    def customer(self,customer_id):
        value=self.request(CUSTOMER,{'id':customer_id}).get('customer')
        if not value or value.get('id')!=customer_id:raise POSRemoteError('Customer association not verified')
        return value

    def read(self,order_id):return self.request(READ,{'id':order_id}).get('order')
    def find(self,reference):
        rows=[];cursor=None;seen=set()
        for _ in range(4):
            data=self.request(FIND,{'query':'tag:pixiepinks-pos-'+reference,'after':cursor});connection=data.get('orders') or {};page=connection.get('pageInfo') or {}
            if not isinstance(connection.get('nodes'),list) or type(page.get('hasNextPage')) is not bool:raise POSRemoteError('Order search incomplete')
            rows.extend(connection['nodes'])
            if not page['hasNextPage']:return [r for r in rows if r.get('sourceIdentifier')==reference]
            cursor=page.get('endCursor')
            if not cursor or cursor in seen:break
            seen.add(cursor)
        raise POSRemoteError('Order search pagination incomplete')
    def create(self,wire):
        data=self.request(CREATE,wire).get('orderCreate') or {}
        if data.get('userErrors')!=[] or not (data.get('order') or {}).get('id'):raise POSRemoteError('Order creation requires reconciliation')
        return data['order']['id']
    def refund(self,wire):
        data=self.request(REFUND,wire).get('refundCreate') or {}
        if data.get('userErrors')!=[] or not (data.get('refund') or {}).get('id'):raise POSRemoteError('Refund outcome requires reconciliation')
        return data['refund']['id']

client=POSShopify()
