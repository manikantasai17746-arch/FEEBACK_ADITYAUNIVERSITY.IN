"""The anonymity sample card (folded corner + stamp animation) is static and self-contained."""
import os
import re

from flask import render_template


def test_anonymity_card_renders_static_sample(app):
    with app.test_request_context('/'):
        html = render_template('_anonymity_card.html')
    assert 'Sample question. Nothing here is saved.' in html
    assert 'Saved with the answer' in html and 'Never attached' in html
    assert 'ADITYA UNIVERSITY' in html and 'ac-stamp' in html and 'ac-fold' in html
    # no scripts, handlers or forms: CSS-only and nothing is submitted/saved
    assert '<script' not in html and 'onclick=' not in html and '<form' not in html
    # the stamp emblem comes from our own static folder (CSP img-src 'self')
    src = re.search(r'href="(/static/img/aditya-emblem\.png[^"]*)"', html)
    assert src
    path = src.group(1).split('?')[0].replace('/static/', '', 1)
    assert os.path.exists(os.path.join(app.static_folder, path))


def test_anonymity_card_css_is_scoped_and_respects_reduced_motion(app):
    css = open(os.path.join(app.static_folder, 'css', 'anonymity_card.css'), encoding='utf-8').read()
    assert 'prefers-reduced-motion' in css
    # every selector is prefixed .ac- (or is inside @keyframes / @media)
    for sel in re.findall(r'^([.#a-z][^{@\n]*)\{', css, re.M):
        for part in sel.split(','):
            assert part.strip().startswith(('.ac-', '0%', '31%', '37%', '14%', '10%', '30%', '24%', '34%', '90%', '96%', '42%', '35%', '33%', '31.5%')), part
