from django import template
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

register = template.Library()
renderer = MarkdownIt("commonmark", {"html": False})


@register.filter
def render_markdown(value: str) -> SafeString:
    return mark_safe(renderer.render(value or ""))
