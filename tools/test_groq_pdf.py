import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_extractor import extract_pdf_metadata

pdf = Path('data/2026/July 2026/03-07-2026/NOC under Regulation 37 Updates/Board Resolution - Persistent Systems Limited.pdf')
result = extract_pdf_metadata(pdf.name, pdf.read_bytes())
print(result.model_dump_json(indent=2))
