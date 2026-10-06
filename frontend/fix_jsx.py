import os
import re

directory = r"C:\Users\Sundaram\Desktop\OCR\current\with qwen 3b\OCR_final\frontend\src\pages\stitch"

for filename in os.listdir(directory):
    if not filename.endswith('.jsx'): continue
    filepath = os.path.join(directory, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix function name with dash
    if "OcrProcessingIn-Flight" in content:
        content = content.replace("function OcrProcessingIn-Flight()", "function OcrProcessingInFlight()")
    
    # Fix style={{ fontVariationSettings: ''FILL' 1' }}
    # We'll just regex replace ''FILL' 1' with '"FILL" 1'
    content = content.replace("''FILL' 1'", "'\"FILL\" 1'")
    
    # Fix <script> tag content that became invalid JSX
    # Actually, any <script> tag in JSX is problematic if it contains raw JS without being in {} or dangerousSetInnerHTML.
    # We can just comment out script tags or remove them.
    content = re.sub(r'<script.*?>.*?</script>', '', content, flags=re.DOTALL)

    # Some onclick="" might be problematic too, let's remove onclick handlers
    content = re.sub(r'onclick="[^"]*"', '', content)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
print("Fixes applied.")
