"""Ingest: raw vendor CSV -> canonical Parquet, with provenance.

Nothing here repairs data. It normalises, records what it found, and refuses
loudly when it cannot proceed. The metadata sidecar exists so a run can always
say which file, which timezone and which column mapping produced its numbers.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .canonical import ARROW_SCHEMA, COLUMNS
from .quality import QualityReport, assess
from .sessions import TimezoneEvidence, detect_timezone, is_conclusive
from .sniff import MappingStore, SchemaGuess, sniff

SYMBOL_RX = re.compile(r"@?([A-Z]{1,4})\b")


@dataclass
class IngestResult:
    source: str
    parquet: str
    symbol: str
    tz: str
    tz_conclusive: bool
    tz_evidence: list[dict]
    schema: dict
    quality: dict
    elapsed_s: float

    def render(self) -> str:
        q = QualityReport(**self.quality)
        head = [
            f"SOURCE   {Path(self.source).name}",
            f"SYMBOL   {self.symbol}",
            f"MAPPING  {SchemaGuess(**self.schema).describe()}",
            f"TZ       {self.tz}   {'confirmed by calendar' if self.tz_conclusive else 'NOT CONCLUSIVE - needs confirmation'}",
            f"CACHE    {Path(self.parquet).name}   ({self.elapsed_s:.1f}s)",
            "",
        ]
        return "\n".join(head) + q.render()


def infer_symbol(path: Path) -> str:
    m = SYMBOL_RX.search(path.stem.upper())
    return m.group(1) if m else path.stem.upper()[:4]


def _read_frame(path: Path, g: SchemaGuess) -> pd.DataFrame:
    """Read only the columns the mapping needs, as strings, then convert."""
    used = list(g.ts_cols) + [g.cols[k] for k in ("open", "high", "low", "close")]
    has_vol = "volume" in g.cols
    if has_vol:
        used.append(g.cols["volume"])

    raw = pd.read_csv(
        path,
        sep=g.delimiter,
        header=0 if g.has_header else None,
        usecols=sorted(set(used)),
        dtype=str,
        engine="c",
    )
    raw.columns = list(range(len(raw.columns))) if not g.has_header else raw.columns
    by_pos = {orig: i for i, orig in enumerate(sorted(set(used)))}

    def col(idx: int) -> pd.Series:
        return raw.iloc[:, by_pos[idx]]

    if len(g.ts_cols) == 1:
        naive = pd.to_datetime(col(g.ts_cols[0]).str.strip(), format=g.ts_format)
    else:
        dfmt, tfmt = g.ts_format.split("|")
        joined = col(g.ts_cols[0]).str.strip() + " " + col(g.ts_cols[1]).str.strip()
        naive = pd.to_datetime(joined, format=f"{dfmt} {tfmt}")

    out = pd.DataFrame({"naive": naive})
    for k in ("open", "high", "low", "close"):
        out[k] = pd.to_numeric(col(g.cols[k]), errors="coerce")
    out["volume"] = pd.to_numeric(col(g.cols["volume"]), errors="coerce") if has_vol else 0
    return out


def ingest(
    path: str | Path,
    cache_dir: str | Path,
    symbol: str | None = None,
    tz_override: str | None = None,
    mapping_store: MappingStore | None = None,
) -> IngestResult:
    """Normalise one raw file into the Parquet cache.

    Raises rather than guessing when the schema cannot be read or the timezone
    cannot be established without an override.
    """
    t0 = time.perf_counter()
    path, cache_dir = Path(path), Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    guess = sniff(path)
    if mapping_store is not None:
        remembered = mapping_store.get(guess)
        if remembered:
            guess.notes.append("column mapping reused from a previous confirmation")
    if not guess.complete:
        raise ValueError(f"{path.name}: cannot read schema -- {'; '.join(guess.notes) or 'unknown layout'}")

    df = _read_frame(path, guess)
    bad = df["naive"].isna() | df[["open", "high", "low", "close"]].isna().any(axis=1)
    if bad.any():
        df = df[~bad].reset_index(drop=True)
        guess.notes.append(f"{int(bad.sum()):,} unparseable rows dropped")

    ranked: list[TimezoneEvidence] = detect_timezone(df["naive"])
    conclusive = is_conclusive(ranked)
    if tz_override:
        tz, conclusive = tz_override, True
    elif conclusive:
        tz = ranked[0].tz
    else:
        raise ValueError(
            f"{path.name}: timezone not conclusive "
            f"(best {ranked[0].tz} at {ranked[0].score:.3f}); confirm it in the UI or pass tz_override"
        )

    utc = pd.DatetimeIndex(df["naive"]).tz_localize(tz, ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    keep = utc.notna()
    if not keep.all():
        guess.notes.append(f"{int((~keep).sum()):,} DST-unresolvable stamps dropped")

    # Resolution-independent epoch seconds. pandas >=3.0 may back a datetime64
    # with microsecond rather than nanosecond units, so dividing a raw int64 by
    # 1e9 is off by 1000x depending on the inferred unit. Cast the resolution
    # explicitly instead of assuming it.
    epoch_s = utc[keep].tz_localize(None).astype("datetime64[s]").astype("int64")

    canon = pd.DataFrame(
        {
            "ts": epoch_s,
            "open": df.loc[keep, "open"].to_numpy("float64"),
            "high": df.loc[keep, "high"].to_numpy("float64"),
            "low": df.loc[keep, "low"].to_numpy("float64"),
            "close": df.loc[keep, "close"].to_numpy("float64"),
            "volume": df.loc[keep, "volume"].fillna(0).astype("int64").to_numpy(),
        }
    ).sort_values("ts", kind="stable").reset_index(drop=True)

    report = assess(canon, tz=tz)
    sym = symbol or infer_symbol(path)
    slug = f"{sym}_{report.bar_seconds // 60}m_{'eth' if report.has_overnight else 'rth'}"
    out_path = cache_dir / f"{slug}.parquet"

    pq.write_table(
        pa.Table.from_pandas(canon[list(COLUMNS)], schema=ARROW_SCHEMA, preserve_index=False),
        out_path,
        compression="zstd",
    )

    result = IngestResult(
        source=str(path),
        parquet=str(out_path),
        symbol=sym,
        tz=tz,
        tz_conclusive=conclusive,
        tz_evidence=[asdict(e) for e in ranked],
        schema=asdict(guess),
        quality=asdict(report),
        elapsed_s=round(time.perf_counter() - t0, 2),
    )
    (cache_dir / f"{slug}.meta.json").write_text(json.dumps(asdict(result), indent=2, default=str))
    return result


def load(parquet: str | Path, columns: tuple[str, ...] = COLUMNS) -> pd.DataFrame:
    """Read cached bars back. Memory-mapped; no copy until touched."""
    return pq.read_table(parquet, columns=list(columns), memory_map=True).to_pandas()
