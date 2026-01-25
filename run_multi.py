import os
import shutil
import requests
from difflib import unified_diff
from bs4 import BeautifulSoup
from targets import TARGETS


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


for target in TARGETS:
    name = target["name"]
    url = target["url"]
    impact = target["impact"]

    today_file = f"today_{name}.txt"
    yesterday_file = f"yesterday_{name}.txt"

    # ファイル名にスペースがあると面倒なので置換
    today_file = today_file.replace(" ", "_")
    yesterday_file = yesterday_file.replace(" ", "_")

    # yesterday ← today
    if os.path.exists(today_file):
        shutil.copyfile(today_file, yesterday_file)

    # 新しい today を取得
    # response = requests.get(url)
    # text = extract_text(response.text)
    text = "TEST CHANGE"


    with open(today_file, "w", encoding="utf-8") as f:
        f.write(text)

    # 差分判定
    if os.path.exists(yesterday_file):
        with open(yesterday_file, "r", encoding="utf-8") as f:
            old = f.readlines()
        with open(today_file, "r", encoding="utf-8") as f:
            new = f.readlines()

        diff = list(unified_diff(old, new))
        if diff:
            result = "変更あり"
        else:
            result = "変更なし"
    else:
        result = "初回"

if result == "変更あり":
    with open("rss_items.txt", "a", encoding="utf-8") as f:
        f.write(f"{impact} | {name} | {url}\n")


    print(f"[{impact}] {name} : {result}")
