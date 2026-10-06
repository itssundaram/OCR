import os
import re
from bs4 import BeautifulSoup

input_dir = 'C:\\Users\\Sundaram\\Desktop\\OCR\\current\\with qwen 3b\\OCR_final\\frontend\\stitch_screens'
output_dir = 'C:\\Users\\Sundaram\\Desktop\\OCR\\current\\with qwen 3b\\OCR_final\\frontend\\src\\pages\\stitch'
os.makedirs(output_dir, exist_ok=True)

def html_to_jsx(html_str):
    # Basic replacements for React JSX
    jsx = html_str.replace('class=', 'className=')
    jsx = jsx.replace('for=', 'htmlFor=')
    jsx = jsx.replace('<!--', '{/*')
    jsx = jsx.replace('-->', '*/}')
    
    # fix self-closing tags
    for tag in ['input', 'img', 'br', 'hr', 'link', 'meta']:
        jsx = re.sub(r'(<' + tag + r'[^>]*)(?<!/)>', r'\1 />', jsx)
        
    # fix style="" attributes (very naive)
    # usually style="width: 50%;" -> style={{ width: '50%' }}
    def style_repl(match):
        style_str = match.group(1)
        rules = [r.strip() for r in style_str.split(';') if r.strip()]
        react_style = []
        for r in rules:
            if ':' in r:
                k, v = r.split(':', 1)
                k = k.strip()
                # camelCase
                parts = k.split('-')
                k = parts[0] + ''.join(p.title() for p in parts[1:])
                react_style.append(f"{k}: '{v.strip()}'")
        return 'style={{ ' + ', '.join(react_style) + ' }}'
        
    jsx = re.sub(r'style="([^"]*)"', style_repl, jsx)
    
    return jsx

for filename in os.listdir(input_dir):
    if not filename.endswith('.html'): continue
    
    filepath = os.path.join(input_dir, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()
        
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    
    if not body: continue
    
    # We want to keep the body's classes in a wrapper div
    body_classes = body.get('class', [])
    body_class_str = ' '.join(body_classes)
    
    inner_html = ""
    for child in body.children:
        inner_html += str(child)
        
    jsx_content = html_to_jsx(inner_html)
    
    component_name = ''.join(word.title() for word in filename.replace('.html', '').split('_'))
    
    jsx_file = f"""import React from 'react';

export default function {component_name}() {{
  return (
    <div className="{body_class_str}">
      {{/* Content extracted from {filename} */}}
      {jsx_content}
    </div>
  );
}}
"""
    
    out_path = os.path.join(output_dir, f"{component_name}.jsx")
    with open(out_path, 'w', encoding='utf-8') as out:
        out.write(jsx_file)
        
print("Conversion complete.")
