# Create this file at: firstcricketapp/templatetags/cricket_extras.py
# (create the templatetags folder if it doesn't exist, and add __init__.py)

from django import template

register = template.Library()

@register.filter
def get_range(value):
    """Returns range(value) so we can loop N times in templates."""
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return range(0)

@register.filter
def split(value, delimiter=','):
    """Split a string by delimiter."""
    return value.split(delimiter)
