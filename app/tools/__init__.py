"""Tool registry: add tools here to make them available to the agent."""

from app.tools.business import get_business_hours
from app.tools.events import get_event_tickets
from app.tools.greetings import add_greeting_occasion
from app.tools.orders import get_order_status, get_orders_by_status
from app.tools.products import get_product_info, search_product
from app.tools.support import create_support_ticket

TOOLS = [
    add_greeting_occasion,
    get_order_status,
    get_orders_by_status,
    search_product,
    get_product_info,
    get_event_tickets,
    create_support_ticket,
    get_business_hours,
]
