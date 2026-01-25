from datetime import datetime

RSS_HEADER = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
<title>AI Platform Change Watcher</title>
<link>http://localhost/</link>
<description>Important changes in AI platforms</description>
"""

RSS_FOOTER = """
</channel>
</rss>
"""

items = ""

try:
    with open("rss_items.txt", "r", encoding="utf-8") as f:
        lines = f.readlines()
except FileNotFoundError:
    lines = []

for line in lines:
    impact, name, url = line.strip().split(" | ")
    pub_date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    items += f"""
<item>
<title>[{impact}] {name}</title>
<link>{url}</link>
<description>Detected change</description>
<pubDate>{pub_date}</pubDate>
</item>
"""

rss = RSS_HEADER + items + RSS_FOOTER

with open("feed.xml", "w", encoding="utf-8") as f:
    f.write(rss)

print("RSS生成完了: feed.xml")
