"""Fixed P1-only transport. Ordinary BarcodeClient.write_empty remains gated."""
import copy
import hashlib
import json
import time
import httpx
from retail_barcode_shopify import BarcodeClient,RemoteError,BARCODE_API_VERSION,MUTATION

IDENTITY={'shop':'eb2b70','product_id':'gid://shopify/Product/9055242223919',
    'variant_id':'gid://shopify/ProductVariant/47281619206447','sku':'1001200',
    'code':'P1','ledger_id':1,'version':2}
# No regulated barcode type, legacy barcode input or unrelated product fields.
PAYLOAD={'productId':IDENTITY['product_id'],'variants':[{'id':IDENTITY['variant_id'],'barcodes':[{'value':'P1'}]}]}
PROTECTED='''query RetailP1Protected($id:ID!,$levels:String,$images:String) {
 productVariant(id:$id) {
  id sku price compareAtPrice taxable taxCode position inventoryPolicy inventoryQuantity
  selectedOptions { name value } image { id url }
  inventoryItem { id sku tracked requiresShipping
   inventoryLevels(first:20,after:$levels) {
    nodes { id location { id } quantities(names:["available","on_hand","committed","incoming","reserved","damaged","safety_stock","quality_control"]) { name quantity } }
    pageInfo { hasNextPage endCursor }
   }
  }
  product { id title handle status descriptionHtml vendor productType tags
   options { id name values } featuredImage { id url }
   images(first:20,after:$images) { nodes { id url } pageInfo { hasNextPage endCursor } }
  }
 }
}'''


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def exact_identity(snapshot):
    return isinstance(snapshot,dict) and all(type(snapshot.get(k)) is type(v) and snapshot.get(k)==v for k,v in IDENTITY.items())


class P1Client(BarcodeClient):
    def __init__(self,http_client=httpx):
        super().__init__(http_client=http_client)
        self.__attempted=False

    def protected(self):
        """Complete location/image connections; missing/denied fields block."""
        levels=images=None;level_done=image_done=False;level_cursors=set();image_cursors=set()
        all_levels=[];all_images=[];baseline=None;deadline=time.monotonic()+60
        for _ in range(100):
            if time.monotonic()>deadline:raise RemoteError('P1_PROTECTED_BUDGET')
            try:
                response=self.http.post(f'{self.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                    headers={'X-Shopify-Access-Token':self.get_token()},
                    json={'query':PROTECTED,'variables':{'id':IDENTITY['variant_id'],'levels':levels,'images':images}},
                    timeout=httpx.Timeout(15,connect=5))
                payload=response.json()
                if time.monotonic()>deadline:raise RemoteError('P1_PROTECTED_BUDGET')
                if response.status_code!=200 or response.headers.get('X-Shopify-API-Version')!=BARCODE_API_VERSION or payload.get('errors'):
                    raise RemoteError('P1_PROTECTED_UNAVAILABLE')
                row=copy.deepcopy(payload['data']['productVariant'])
                if set(row)!=set('id sku price compareAtPrice taxable taxCode position inventoryPolicy inventoryQuantity selectedOptions image inventoryItem product'.split()):raise ValueError()
                item=row['inventoryItem'];product=row['product']
                if set(item)!=set('id sku tracked requiresShipping inventoryLevels'.split()) or set(product)!=set('id title handle status descriptionHtml vendor productType tags options featuredImage images'.split()):raise ValueError()
                if row['id']!=IDENTITY['variant_id'] or row['sku']!=IDENTITY['sku'] or product['id']!=IDENTITY['product_id'] or product['status']!='ACTIVE':raise ValueError()
                if not isinstance(row['price'],str) or not isinstance(row['inventoryQuantity'],int) or not isinstance(item['id'],str):raise ValueError()
                for key in ('selectedOptions',):
                    if not isinstance(row[key],list):raise ValueError()
                if not isinstance(product['tags'],list) or not isinstance(product['options'],list):raise ValueError()
                lc=item.pop('inventoryLevels');ic=product.pop('images')
                if baseline is not None and row!=baseline:raise RemoteError('P1_PROTECTED_CHANGED_DURING_PAGES')
                baseline=row
                for kind,connection,done,cursors,values in [('levels',lc,level_done,level_cursors,all_levels),('images',ic,image_done,image_cursors,all_images)]:
                    nodes,page=self._connection(connection,limit=20)
                    if done:continue
                    for node in nodes:
                        if not isinstance(node,dict) or not isinstance(node.get('id'),str) or any(n['id']==node['id'] for n in values):raise ValueError()
                        if kind=='levels':
                            if set(node)!= {'id','location','quantities'} or not isinstance(node['location'],dict) or not isinstance(node['location'].get('id'),str):raise ValueError()
                            quantities=node['quantities']
                            if not isinstance(quantities,list) or len(quantities)!=8 or {q.get('name') for q in quantities}!={'available','on_hand','committed','incoming','reserved','damaged','safety_stock','quality_control'} or any(type(q.get('quantity')) is not int for q in quantities):raise ValueError()
                        elif set(node)!={'id','url'} or not isinstance(node['url'],str):raise ValueError()
                        values.append(node)
                    if len(values)>10000:raise RemoteError('P1_PROTECTED_BUDGET')
                    after=self._next_cursor(page,cursors) if page['hasNextPage'] else None
                    if kind=='levels':levels=after;level_done=not page['hasNextPage']
                    else:images=after;image_done=not page['hasNextPage']
                if level_done and image_done:
                    baseline['inventoryItem']['inventoryLevels']=all_levels
                    baseline['product']['images']=all_images
                    return baseline
            except RemoteError:raise
            except Exception:raise RemoteError('P1_PROTECTED_UNAVAILABLE') from None
        raise RemoteError('P1_PROTECTED_BUDGET')

    def _attempt_p1(self,permit):
        # Only the service can issue an ephemeral permit after durable intent.
        from retail_barcode_p1_pilot import _Permit
        if type(permit) is not _Permit or not permit.consume() or self.__attempted:
            raise RemoteError('P1_ATTEMPT_NOT_AUTHORIZED')
        self.__attempted=True
        if self.base_url!='https://eb2b70.myshopify.com':raise RemoteError('P1_SHOP_MISMATCH')
        # Exactly one POST. No inherited retrying GraphQL mutation helper.
        try:
            token=self.get_token()
            # Token refresh can itself use the network. Recheck the original
            # final-target deadline after authentication, before the sole POST.
            if not permit.live():raise RemoteError('P1_FINAL_CHECK_EXPIRED')
            response=self.http.post(f'{self.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':token},json={'query':MUTATION,'variables':copy.deepcopy(PAYLOAD)},
                timeout=httpx.Timeout(15,connect=5))
            value=response.json();result=value.get('data',{}).get('productVariantsBulkUpdate')
            if response.status_code!=200 or response.headers.get('X-Shopify-API-Version')!=BARCODE_API_VERSION or value.get('errors') or not isinstance(result,dict) or result.get('userErrors')!=[] or result.get('productVariants')!=[{'id':IDENTITY['variant_id']}] or (result.get('product') or {}).get('id')!=IDENTITY['product_id']:
                raise RemoteError('P1_OUTCOME_UNCERTAIN')
            return True
        except Exception:raise RemoteError('P1_OUTCOME_UNCERTAIN') from None
