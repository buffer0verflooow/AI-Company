#!/usr/bin/env python3
"""CSS inliner for WeChat articles. Converts <style> blocks to inline styles.

**DEPRECATED for production use — use premailer instead.**

This script has a known pitfall: CSS values containing double quotes
(e.g. font-family: "PingFang SC") MUST be sanitized before injection into
HTML style="..." attributes, otherwise the quotes terminate the attribute
early and spill CSS fragments as orphan HTML attributes.

For production pushes, use wechat_push.py which uses premailer.transform()
— a mature CSS inlining library that handles all edge cases correctly.

This script is retained for quick local testing only.
"""

import re, sys

def parse_styles(css_text):
    """Parse CSS text into {selector: {property: value}} dict."""
    rules = {}
    # Remove comments and whitespace-collapse
    css_text = re.sub(r'/\*.*?\*/', '', css_text, flags=re.S)
    
    # Split by closing brace
    blocks = css_text.split('}')
    for block in blocks:
        block = block.strip()
        if not block or '{' not in block:
            continue
        selector_part, props_part = block.split('{', 1)
        selectors = [s.strip() for s in selector_part.split(',')]
        props = {}
        for prop in props_part.split(';'):
            prop = prop.strip()
            if ':' in prop:
                k, v = prop.split(':', 1)
                props[k.strip()] = v.strip()
        for sel in selectors:
            if sel:
                rules[sel] = props
    return rules


def inline_css_in_html(html):
    """Find <style>...</style> in HTML and inline all rules into elements."""
    # Extract style block
    style_match = re.search(r'<style>(.*?)</style>', html, re.S)
    if not style_match:
        return html
    
    css_text = style_match.group(1)
    rules = parse_styles(css_text)
    
    # Remove style block from HTML
    html = html[:style_match.start()] + html[style_match.end():]
    
    # Simple rule: apply styles to matching tags
    # For body-level: apply to the wrapper
    # For specific elements: find and add style
    
    # Start with body rules as default styles
    body_rules = rules.get('body, p, li, table, blockquote', {})
    
    def get_style_for_tag(tag, rules):
        """Get merged styles for a tag."""
        styles = {}
        # Check exact tag match
        if tag in rules:
            styles.update(rules[tag])
        # Check compound selectors
        for sel, props in rules.items():
            if tag in sel:
                styles.update(props)
        return styles
    
    def add_style_to_tag(match):
        tag = match.group(1)
        existing = match.group(2) or ''
        styles = get_style_for_tag(tag, rules)
        if not styles:
            return match.group(0)
        
        # Merge with existing inline styles
        style_str = '; '.join(f'{k}: {v}' for k, v in styles.items())
        if existing:
            style_str = existing.rstrip('" \'') + '; ' + style_str
        
        return f'<{tag} style="{style_str}"'
    
    # Apply inline styles to tags that have rules
    for tag in ['body', 'p', 'h1', 'h2', 'h3', 'pre', 'code', 'blockquote', 'table', 'th', 'td', 'img', 'a', 'hr', 'li']:
        tag_rules = get_style_for_tag(tag, rules)
        if not tag_rules:
            continue
        style_str = '; '.join(f'{k}: {v}' for k, v in tag_rules.items())
        
        # Add style to opening tags
        pattern = f'<{tag}>|<{tag} '
        def replacer(m):
            full = m.group(0)
            if full.endswith('>'):
                new = f'<{tag} style="{style_str}">'
            else:
                # Check if it already has style=
                rest = full[len(f'<{tag} '):]
                if 'style=' in rest:
                    # Append to existing style
                    return re.sub(r'style="([^"]*)"', f'style="\\1; {style_str}"', full)
                else:
                    new = f'<{tag} style="{style_str}" {rest}'
            return new
        
        html = re.sub(pattern, replacer, html)
    
    return html


def _sanitize_style_value(style_str):
    """Strip ALL double quotes from CSS values so they don't break
    HTML style="..." attributes.  WeChat renders styles correctly without
    quotes around font names; the quotes are syntactic sugar for CSS files
    but poison HTML attributes."""
    # font-family: "PingFang SC", "Microsoft YaHei", sans-serif
    # becomes font-family: PingFang SC, Microsoft YaHei, sans-serif
    return style_str.replace('"', '')


def inline_simple(html):
    """Inlines <style> block CSS into every matching element as style= attributes.
    
    WHY: WeChat strips <style> tags from article content. The ONLY way to get
    CSS to survive is as inline style="..." on every HTML element.
    
    PITFALL: CSS font-family values often contain double quotes (\"PingFang SC\").
    These must be stripped before injection, otherwise they terminate the HTML
    style attribute early and the formatting breaks entirely.
    """
    # Extract CSS
    style_match = re.search(r'<style>(.*?)</style>', html, re.S)
    if not style_match:
        return html
    
    css_text = style_match.group(1)
    html = html[:style_match.start()] + html[style_match.end():]
    
    rules = parse_styles(css_text)
    
    # Map CSS selectors to HTML tags
    selector_map = {
        'body, p, li, table, blockquote': ['p', 'li', 'table', 'blockquote'],
        'body': ['body'],
        'p': ['p'],
        'h1': ['h1'],
        'h2': ['h2'],
        'h3': ['h3'],
        'pre': ['pre'],
        'pre code': [],
        'code': ['code'],
        'blockquote': ['blockquote'],
        'blockquote p': ['blockquote p'],
        'table': ['table'],
        'th, td': ['th', 'td'],
        'th': ['th'],
        'td': ['td'],
        'img': ['img'],
        'a': ['a'],
        'hr': ['hr'],
    }
    
    for selector, tags in selector_map.items():
        if selector not in rules:
            continue
        props = rules[selector]
        style_str = '; '.join(f'{k}: {v}' for k, v in props.items())
        # CRITICAL: strip double quotes from CSS values before injecting
        # into HTML style="..." attribute, otherwise e.g.
        # font-family: "PingFang SC" breaks the attribute boundary
        style_str = _sanitize_style_value(style_str)
        
        for tag in tags:
            pattern = re.compile(f'<{tag}(>|\\\\s)', re.IGNORECASE)
            def replacer(m, t=tag, s=style_str):
                prefix = m.group(0)
                if prefix.endswith('>'):
                    return f'<{t} style="{s}">'
                else:
                    rest = prefix[len(f'<{t} '):]
                    return f'<{t} style="{s}" {rest}'
            html = pattern.sub(replacer, html)
    
    return html


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: wechat_css_inline.py <html_file>")
        sys.exit(1)
    
    with open(sys.argv[1]) as f:
        html = f.read()
    
    result = inline_simple(html)
    
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w') as f:
            f.write(result)
        print(f"Inlined CSS written to {sys.argv[2]}")
    else:
        print(result)
