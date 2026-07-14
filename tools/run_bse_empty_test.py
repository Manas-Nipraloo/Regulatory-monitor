import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tasks.daily_monitor import run_daily_monitor

async def main():
    result = await run_daily_monitor(
        run_date=date(2026, 7, 4),
        site_filters=["NOC under Regulation 37 Updates"],
    )
    print(result.model_dump_json(indent=2))

asyncio.run(main())
