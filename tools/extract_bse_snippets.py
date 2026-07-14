from pathlib import Path

text = Path('tools/main-bse.js').read_text(encoding='utf-8')
for needle in ['GetNOC', 'NOCUnder', 'Regulation 37']:
    print('NEEDLE', needle)
    start = 0
    for _ in range(8):
        index = text.lower().find(needle.lower(), start)
        if index == -1:
            break
        print('---', index)
        print(text[max(0, index - 700):index + 1200])
        start = index + len(needle)
