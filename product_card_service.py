"""Shared customer-facing image-card formatting for staff and deterministic flows."""
from models import WhatsAppConversationMessage as Message


def caption(product):
    variant_line=(f"\n{product['variant_title']}" if product.get('variant_title') and product['variant_title']!='Default Title' else '')
    return f"{product['title']}{variant_line}\nPrice: Rs. {product['price']:,.2f}\nView Product: {product['url']}"


def message(phone,product,provider,*,source='staff_product',actor=None):
    return Message(phone_number=phone,direction='outbound',message_text=caption(product),
        whatsapp_message_id=provider,response_kind=source,send_status='sent',sent_by=actor,
        analytics_actor_kind='automatic' if source=='question_product' else None,
        analytics_category='products',shopify_product_reference=product['id'],shopify_variant_reference=product['variant_id'],
        product_handle=product['handle'],product_title=product['title'],variant_title=product.get('variant_title'),
        product_sku=product.get('sku'),product_price=product['price'],product_currency=product['currency'],
        product_image_url=product['image'],product_url=product['url'])
