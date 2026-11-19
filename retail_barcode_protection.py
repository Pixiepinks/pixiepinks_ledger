"""Complete protected product reads shared by the normal release and pilot evidence."""
import copy
import time
import httpx
from retail_barcode_shopify import RemoteError,BARCODE_API_VERSION

PROTECTED='''query RetailBarcodeProtected($id:ID!,$levels:String,$images:String) {
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

def read_protected(client,snapshot):
    """Complete location/image connections; missing/denied fields block."""
    levels=images=None;level_done=image_done=False;level_cursors=set();image_cursors=set()
    all_levels=[];all_images=[];baseline=None;deadline=time.monotonic()+60
    for _ in range(100):
        if time.monotonic()>deadline:raise RemoteError('BARCODE_PROTECTED_BUDGET')
        try:
            response=client.http.post(f'{client.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':client.get_token()},
                json={'query':PROTECTED,'variables':{'id':snapshot['variant_id'],'levels':levels,'images':images}},
                timeout=httpx.Timeout(15,connect=5))
            payload=response.json()
            if time.monotonic()>deadline:raise RemoteError('BARCODE_PROTECTED_BUDGET')
            if response.status_code!=200 or response.headers.get('X-Shopify-API-Version')!=BARCODE_API_VERSION or payload.get('errors'):
                raise RemoteError('BARCODE_PROTECTED_UNAVAILABLE')
            row=copy.deepcopy(payload['data']['productVariant'])
            if set(row)!=set('id sku price compareAtPrice taxable taxCode position inventoryPolicy inventoryQuantity selectedOptions image inventoryItem product'.split()):raise ValueError()
            item=row['inventoryItem'];product=row['product']
            if set(item)!=set('id sku tracked requiresShipping inventoryLevels'.split()) or set(product)!=set('id title handle status descriptionHtml vendor productType tags options featuredImage images'.split()):raise ValueError()
            if row['id']!=snapshot['variant_id'] or (row['sku'] or '')!=(snapshot.get('sku') or '') or product['id']!=snapshot['product_id'] or product['status']!='ACTIVE':raise ValueError()
            if not isinstance(row['price'],str) or not (type(row['inventoryQuantity']) is int or (row['inventoryQuantity'] is None and item['tracked'] is False)) or not isinstance(item['id'],str):raise ValueError()
            for key in ('selectedOptions',):
                if not isinstance(row[key],list):raise ValueError()
            if not isinstance(product['tags'],list) or not isinstance(product['options'],list):raise ValueError()
            lc=item.pop('inventoryLevels');ic=product.pop('images')
            if baseline is not None and row!=baseline:raise RemoteError('BARCODE_PROTECTED_CHANGED_DURING_PAGES')
            baseline=row
            for kind,connection,done,cursors,values in [('levels',lc,level_done,level_cursors,all_levels),('images',ic,image_done,image_cursors,all_images)]:
                nodes,page=client._connection(connection,limit=20)
                if done:continue
                for node in nodes:
                    if not isinstance(node,dict) or not isinstance(node.get('id'),str) or any(n['id']==node['id'] for n in values):raise ValueError()
                    if kind=='levels':
                        if set(node)!= {'id','location','quantities'} or not isinstance(node['location'],dict) or not isinstance(node['location'].get('id'),str):raise ValueError()
                        quantities=node['quantities']
                        if not isinstance(quantities,list) or len(quantities)!=8 or {q.get('name') for q in quantities}!={'available','on_hand','committed','incoming','reserved','damaged','safety_stock','quality_control'} or any(type(q.get('quantity')) is not int for q in quantities):raise ValueError()
                    elif set(node)!={'id','url'} or not isinstance(node['url'],str):raise ValueError()
                    values.append(node)
                if len(values)>10000:raise RemoteError('BARCODE_PROTECTED_BUDGET')
                after=client._next_cursor(page,cursors) if page['hasNextPage'] else None
                if kind=='levels':levels=after;level_done=not page['hasNextPage']
                else:images=after;image_done=not page['hasNextPage']
            if level_done and image_done:
                baseline['inventoryItem']['inventoryLevels']=all_levels
                baseline['product']['images']=all_images
                return baseline
        except RemoteError:raise
        except Exception:raise RemoteError('BARCODE_PROTECTED_UNAVAILABLE') from None
    raise RemoteError('BARCODE_PROTECTED_BUDGET')
