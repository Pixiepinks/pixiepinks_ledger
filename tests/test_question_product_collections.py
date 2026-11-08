from fastapi import HTTPException
import pytest
import shopify_catalog_service as catalogue
import shopify_collection_service as collections
from question_product_catalogue import collection_products
from test_customer_questions import questions,inbox
from test_shopify_collection_sends import product,listing

BASE='/crm/quick-messages/customer-questions'
COLLECTION='gid://shopify/Collection/20'


def test_admin_collection_discovery_and_products_scoped_to_collection(questions,monkeypatch):
    v=questions;seen=[]
    monkeypatch.setattr(catalogue,'discover_collections',lambda q,limit:seen.append((q,limit)) or [{'id':COLLECTION,'title':'Size 12 bicycles'}])
    def load(cid):seen.append(cid);return listing([product(1),product(2,title='Gift pack')])
    monkeypatch.setattr(collections,'load_collection',load)
    response=v[0].get(BASE+'/collections',params={'q':'Size 12'});assert response.status_code==200
    assert response.json()['collections'][0]['id']==COLLECTION and seen[0]==('Size 12',100)
    response=v[0].get(BASE+'/products',params={'collection_id':COLLECTION,'q':'Gift pack'})
    assert response.status_code==200 and seen[-1]==COLLECTION
    data=response.json();assert len(data['products'])==1 and data['products'][0]['id'].endswith('/2')
    assert data['products'][0]['variants'][0]['id'].endswith('/201') and data['products'][0]['variants'][0]['available']
    assert data['has_next_page'] is False and data['excluded_products']==1
    assert v[0].get(BASE+'/products',params={'collection_id':'https://evil.invalid'}).status_code==422
    v[4]('quick-staff')
    assert v[0].get(BASE+'/collections').status_code==403
    assert v[0].get(BASE+'/products',params={'collection_id':COLLECTION}).status_code==403


def test_collection_failure_is_specific_and_releases_slot(monkeypatch):
    def fail(*a):raise collections.CollectionError('This collection exceeds 200 products. Use a smaller collection; no partial result was selected.')
    monkeypatch.setattr(collections,'load_collection',fail)
    with pytest.raises(HTTPException) as error:collection_products(COLLECTION)
    assert error.value.status_code==422 and '200 products' in error.value.detail
    slots=[]
    try:
        for _ in range(4):assert collections.SLOTS.acquire(blocking=False);slots.append(1)
    finally:
        for _ in slots:collections.SLOTS.release()
