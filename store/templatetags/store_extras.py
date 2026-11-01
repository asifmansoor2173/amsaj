from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def inr(value):
    """Format a number as Indian rupees with lakh/crore grouping: 125000 -> ₹1,25,000."""
    if value in (None, ""):
        return ""
    amount = int(Decimal(value).quantize(Decimal("1")))
    digits = str(abs(amount))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{'-' if amount < 0 else ''}₹{digits}"


@register.simple_tag(takes_context=True)
def query_without(context, *names, **updates):
    """Rebuild the current query string, dropping some keys (e.g. 'page') and setting others."""
    params = context["request"].GET.copy()
    for name in names:
        params.pop(name, None)
    for key, value in updates.items():
        params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
