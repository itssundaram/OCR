import json
import urllib.request
import os

with open('C:\\Users\\Sundaram\\.gemini\\antigravity-ide\\brain\\c38f4c21-75cc-4ff3-b76f-0910eb6f8a48\\.system_generated\\steps\\103\\output.txt', 'r') as f:
    data = json.load(f)

output_dir = 'C:\\Users\\Sundaram\\Desktop\\OCR\\current\\with qwen 3b\\OCR_final\\frontend\\stitch_screens'
os.makedirs(output_dir, exist_ok=True)

for screen in data['screens']:
    title = screen['title'].split(' - ')[0].replace(' ', '_').replace('&', 'and').lower()
    url = screen['htmlCode']['downloadUrl']
    print(f"Downloading {title}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            with open(os.path.join(output_dir, f"{title}.html"), 'w', encoding='utf-8') as out:
                out.write(html)
    except Exception as e:
        print(f"Failed to download {title}: {e}")

print("Done.")
