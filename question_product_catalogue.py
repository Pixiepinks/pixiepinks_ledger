"""Adapt bounded, verified Shopify collection discovery to the selection picker."""
from fastapi import HTTPException
import shopify_collection_service as collections
from shopify_catalog_service import ShopifyCatalogError


def collection_products(collection_id,query=''):
    if not collections.SLOTS.acquire(blocking=False):raise HTTPException(429,'Catalogue is busy. Please try again shortly.')
    try:
        try:data=collections.load_collection(collection_id)
        except collections.CollectionError as error:raise HTTPException(422,str(error)) from None
        except ShopifyCatalogError:raise HTTPException(503,'Collection products are temporarily unavailable') from None
    finally:collections.SLOTS.release()
    terms=query.casefold().split();products=[]
    for product in data['products']:
        searchable=' '.join([product['title'],*(str(v.get(field) or '') for v in product['variants'] for field in ['variant_title','sku','handle'])]).casefold()
        if terms and not all(term in searchable for term in terms):continue
        variants=[{'id':v['variant_id'],'title':v['variant_title'],'sku':v.get('sku',''),'price':v['price'],
            'available':True,'image':v['image']} for v in product['variants']]
        products.append({'id':product['id'],'title':product['title'],'status':'ACTIVE','published':True,
            'image':variants[0]['image'],'product_type':'','variants':variants})
    return {'products':products,'collection':data['collection'],'excluded_products':data['excluded_products'],
        'has_next_page':False,'next_cursor':None}
