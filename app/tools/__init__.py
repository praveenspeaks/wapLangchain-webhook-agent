"""Tool registry: add tools here to make them available to the agent.

The chat agent answers anyone who messages the business, so only public business
tools belong here. Birthdays, anniversaries, greetings, summaries and captured
wishes are private to the owner and are reached only through owner commands in
their own WhatsApp chat (app/whatsapp/store.py), whose replies go privately to
WHATSAPP_OWNER_NUMBER. add_greeting_occasion is therefore deliberately NOT listed.
"""

from app.tools.business import get_business_hours
from app.tools.events import get_event_tickets
from app.tools.orders import get_order_status, get_orders_by_status
from app.tools.products import get_product_info, search_product
from app.tools.support import create_support_ticket

TOOLS = [
    get_order_status,
    get_orders_by_status,
    search_product,
    get_product_info,
    get_event_tickets,
    create_support_ticket,
    get_business_hours,
]
