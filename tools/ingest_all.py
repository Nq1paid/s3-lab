import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.data.loader import ingest, load
from engine.data.sniff import MappingStore

store = MappingStore("data/cache/mappings.json")
for src in sorted(Path("data/raw").glob("*.csv")):
    print("=" * 78)
    try:
        r = ingest(src, "data/cache", mapping_store=store)
        print(r.render())
        t0 = time.perf_counter()
        df = load(r.parquet)
        print(f"\n  reload from parquet  {len(df):,} bars in {time.perf_counter()-t0:.2f}s"
              f"   ({Path(r.parquet).stat().st_size/1e6:.1f} MB on disk)")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
    print()
