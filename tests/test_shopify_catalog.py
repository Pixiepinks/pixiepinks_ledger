import logging

import httpx
import pytest

import main
import shopify_catalog_service as shopify
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(status, payload):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://test"))


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(shopify.settings, "SHOPIFY_SHOP", "test-shop")
    monkeypatch.setattr(shopify.settings, "SHOPIFY_CLIENT_ID", "client-id")
    monkeypatch.setattr(shopify.settings, "SHOPIFY_CLIENT_SECRET", "super-secret")
    monkeypatch.setattr(shopify.settings, "SHOPIFY_API_VERSION", "2026-07")


def token_payload(token="temporary-token", expires=86399):
    return response(200, {"access_token": token, "expires_in": expires, "scope": "read_products"})


def test_age_is_not_parsed_as_wheel_size_but_explicit_size_is():
    assert shopify.parse_filters("12 years")["size"] is None
    assert shopify.parse_filters("bicycle for my 12 year old boy")["size"] is None
    assert shopify.parse_filters("size 20 bicycles")["size"] == "20"
    assert shopify.parse_filters('20" bicycles')["size"] == "20"


def graphql_payload():
    return response(200, {"data": {"products": {"nodes": [{
        "id": "gid://shopify/Product/1", "title": "Lumala Pixie", "handle": "lumala-pixie",
        "productType": "Bicycle", "vendor": "Lumala", "tags": ["pink"], "status": "ACTIVE",
        "featuredImage": {"url": "https://cdn.example/pink.jpg"},
        "variants": {"nodes": [
            {"id": "v1", "title": "Pink / 20 inch", "sku": "LP20", "price": "39999.00",
             "availableForSale": True, "inventoryQuantity": 2,
             "inventoryItem": {"tracked": True},
             "selectedOptions": [{"name": "Color", "value": "Pink"}, {"name": "Size", "value": "20 inch"}]},
            {"id": "v2", "title": "Pink / 16 inch", "sku": "LP16", "price": "45000.00",
             "availableForSale": False, "inventoryQuantity": 0,
             "inventoryItem": {"tracked": True},
             "selectedOptions": [{"name": "Size", "value": "16 inch"}]},
        ]}
    }]}}})


def test_token_request_shape_cache_and_secret_not_logged(caplog):
    fake = FakeHTTP([token_payload()])
    client = ShopifyCatalogClient(fake, clock=lambda: 1000)
    with caplog.at_level(logging.DEBUG):
        assert client.get_token() == "temporary-token"
        assert client.get_token() == "temporary-token"
    assert len(fake.calls) == 1
    url, kwargs = fake.calls[0]
    assert url == "https://test-shop.myshopify.com/admin/oauth/access_token"
    assert kwargs["data"] == {"grant_type": "client_credentials", "client_id": "client-id",
                              "client_secret": "super-secret"}
    assert kwargs["headers"] == {"Content-Type": "application/x-www-form-urlencoded"}
    assert "super-secret" not in caplog.text
    assert "temporary-token" not in caplog.text


def test_token_refreshes_inside_safety_margin():
    now = [1000]
    fake = FakeHTTP([token_payload("one", 600), token_payload("two", 600)])
    client = ShopifyCatalogClient(fake, clock=lambda: now[0])
    assert client.get_token() == "one"
    now[0] = 1301
    assert client.get_token() == "two"
    assert len(fake.calls) == 2


def test_401_refreshes_and_retries_exactly_once():
    fake = FakeHTTP([token_payload("old"), response(401, {}), token_payload("new"),
                     response(200, {"data": {"shop": {"name": "PixiePinks"}}})])
    client = ShopifyCatalogClient(fake)
    assert client.graphql("query { shop { name } }", {})["shop"]["name"] == "PixiePinks"
    assert len(fake.calls) == 4
    assert fake.calls[1][1]["headers"]["X-Shopify-Access-Token"] == "old"
    assert fake.calls[3][1]["headers"]["X-Shopify-Access-Token"] == "new"


def test_second_401_is_safe_error_without_endless_retry():
    fake = FakeHTTP([token_payload("old"), response(401, {}), token_payload("new"), response(401, {})])
    with pytest.raises(ShopifyCatalogError, match="authentication failed"):
        ShopifyCatalogClient(fake).graphql("query { shop { name } }", {})
    assert len(fake.calls) == 4


def test_missing_configuration_fails_gracefully(monkeypatch):
    monkeypatch.setattr(shopify.settings, "SHOPIFY_CLIENT_SECRET", None)
    with pytest.raises(ShopifyCatalogError, match="configuration"):
        ShopifyCatalogClient(FakeHTTP([])).get_token()


def test_search_normalizes_filters_price_size_and_availability(monkeypatch):
    fake = FakeHTTP([token_payload(), graphql_payload()])
    monkeypatch.setattr(shopify, "client", ShopifyCatalogClient(fake))
    products = shopify.search_products('bicycles size 20 under Rs. 40,000', limit=5)
    assert len(products) == 1
    assert products[0]["title"] == "Lumala Pixie"
    assert products[0]["url"] == "https://www.pixiepinks.shop/products/lumala-pixie"
    assert products[0]["featured_image"] == "https://cdn.example/pink.jpg"
    assert products[0]["variants"] == [{
        "id": "v1", "title": "Pink / 20 inch", "sku": "LP20", "price": "39999.00",
        "currency": "LKR", "available": True, "inventory_quantity": 2,
        "inventory_tracked": True, "selected_options": {"Color": "Pink", "Size": "20 inch"},
    }]
    graph_call = fake.calls[1]
    assert graph_call[0].endswith("/admin/api/2026-07/graphql.json")
    assert graph_call[1]["json"]["variables"]["first"] == shopify.MAX_CANDIDATES
    assert "X-Shopify-Access-Token" in graph_call[1]["headers"]


@pytest.mark.parametrize("customer_text", [
    "Do you have bicycles?",
    "Do you have bikes?",
    "Show me size 20 bicycles",
    "bicycles under Rs 40000",
    "pink bicycles",
])
def test_bicycle_customer_phrases_find_mocked_shopify_product(monkeypatch, customer_text):
    fake = FakeHTTP([token_payload(), graphql_payload()])
    monkeypatch.setattr(shopify, "client", ShopifyCatalogClient(fake))

    products = shopify.search_products(customer_text)

    assert [product["title"] for product in products] == ["Lumala Pixie"]
    search = fake.calls[1][1]["json"]["variables"]["query"]
    assert "title:bicycle*" in search
    assert "product_type:bicycle*" in search
    assert "vendor:bicycle*" in search
    assert "tag:bicycle*" in search
    assert "title:bike*" in search
    assert "Do" not in search and "40000" not in search and "pink" not in search


@pytest.mark.parametrize("customer_text", [
    "bicycles", "Do you have bicycles?", "Show me size 20 bicycles",
])
def test_exact_bicycle_shopify_search_syntax(customer_text):
    expected = (
        "(title:bicycle* OR product_type:bicycle* OR vendor:bicycle* OR tag:bicycle* "
        "OR title:bike* OR product_type:bike* OR vendor:bike* OR tag:bike*)"
    )
    assert shopify._shopify_search_query(customer_text) == expected


@pytest.mark.parametrize("field", ["title", "productType", "vendor", "tags"])
def test_bicycle_candidate_can_match_each_supported_product_field(monkeypatch, field):
    payload = graphql_payload().json()
    node = payload["data"]["products"]["nodes"][0]
    node.update({"title": "Pixie", "productType": "Kids", "vendor": "Acme", "tags": []})
    node[field] = ["bicycles"] if field == "tags" else "Bicycles"
    fake = FakeHTTP([token_payload(), response(200, payload)])
    monkeypatch.setattr(shopify, "client", ShopifyCatalogClient(fake))
    assert shopify.search_products("Do you have bikes?")[0]["handle"] == "lumala-pixie"


def test_safe_catalog_diagnostic_uses_unfiltered_products_query(caplog):
    payload = {"data": {"products": {"nodes": [{
        "title": "Pixie Bicycle", "productType": "Bicycle", "vendor": "PixiePinks",
        "handle": "pixie-bicycle",
    }]}}}
    fake = FakeHTTP([token_payload(), response(200, payload)])
    with caplog.at_level(logging.INFO):
        report = shopify.diagnose_catalog_connectivity(ShopifyCatalogClient(fake))
    assert report == {
        "authentication": "success", "graphql": "success", "product_count": 1,
        "products": [{"title": "Pixie Bicycle", "product_type": "Bicycle",
                      "vendor": "PixiePinks", "handle": "pixie-bicycle"}],
    }
    graphql_call = fake.calls[1][1]["json"]
    assert graphql_call["variables"] == {}
    assert "products(first: 5)" in graphql_call["query"]
    assert "$query" not in graphql_call["query"]
    assert "super-secret" not in caplog.text
    assert "temporary-token" not in caplog.text


def test_safe_catalog_diagnostic_reports_authentication_failure():
    fake = FakeHTTP([response(401, {})])
    assert shopify.diagnose_catalog_connectivity(ShopifyCatalogClient(fake)) == {
        "authentication": "failure", "graphql": "not_attempted", "product_count": 0,
        "products": [],
    }


@pytest.mark.parametrize("text, expected", [
    ("under Rs 40000", "40000"), ("below Rs. 40,000", "40000"),
    ("less than LKR 40000", "40000"), ("රු 40000 ට අඩු", "40000"),
])
def test_lkr_max_price_parsing(text, expected):
    assert shopify.parse_filters(text)["max_price"] == shopify.Decimal(expected)


def test_between_and_no_match_filters(monkeypatch):
    filters = shopify.parse_filters("between 30,000 and 50,000")
    assert filters["min_price"] == shopify.Decimal("30000")
    assert filters["max_price"] == shopify.Decimal("50000")
    fake = FakeHTTP([token_payload(), graphql_payload()])
    monkeypatch.setattr(shopify, "client", ShopifyCatalogClient(fake))
    assert shopify.search_products("bicycle size 24") == []


def test_untracked_inventory_does_not_claim_quantity():
    node = graphql_payload().json()["data"]["products"]["nodes"][0]
    node["variants"]["nodes"][0]["inventoryItem"]["tracked"] = False
    normalized = shopify._normalize_product(node, shopify.parse_filters("size 20"))
    assert normalized["variants"][0]["inventory_quantity"] is None
    assert normalized["variants"][0]["available"] is True


def test_timeout_is_wrapped_safely():
    request = httpx.Request("POST", "https://test-shop.myshopify.com")
    fake = FakeHTTP([token_payload(), httpx.ReadTimeout("private detail", request=request)])
    with pytest.raises(ShopifyCatalogError, match="catalogue request failed") as exc:
        ShopifyCatalogClient(fake).graphql("query {}", {})
    assert "private detail" not in str(exc.value)


def test_product_intent_greeting_followup_and_sinhala():
    assert main.needs_product_catalog("Do you have bicycles?")
    assert main.needs_product_catalog("රු 40000 ට අඩු bicycle තියෙනවද?")
    assert not main.needs_product_catalog("Hello")
    assert not main.needs_product_catalog("Where are you located?")
    history = [{"direction": "inbound", "message_text": "Show me school bags"}]
    assert main.needs_product_catalog("How much is the second one?", history)


def test_colour_filter():
    node = graphql_payload().json()["data"]["products"]["nodes"][0]
    assert shopify._normalize_product(node, shopify.parse_filters("pink bicycle"))
    assert shopify._normalize_product(node, shopify.parse_filters("blue bicycle")) is None
