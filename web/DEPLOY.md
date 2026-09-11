# Putting the UI on Vercel

The folder to upload is **`web/public`**. Nothing else.

## Once

1. Go to [vercel.com/new](https://vercel.com/new).
2. Drag the `web/public` folder onto the page. There is no build step, no
   framework to pick, no environment variable to set — it is four static files.
3. Vercel gives you a URL like `https://s3-lab.vercel.app`. That is the app.

Bookmark it. It works from any browser on any machine you are signed into.

## Every time you want to run a backtest

Double-click **Backtest Lab** on your Desktop first. That starts the engine on
this PC and the site connects to it by itself — the dot in the top right goes
from `ENGINE OFFLINE` to `ENGINE LIVE`.

Open the Vercel URL and use it normally.

## Why the engine is not on Vercel

It cannot be, and it is worth knowing exactly why rather than assuming it is a
choice someone made lazily:

| | this project | Vercel serverless |
|---|---|---|
| One raw export | 169 MB | 4.5 MB request body limit |
| Cached series | 61 MB of memory-mapped Parquet | read-only disk, 512 MB `/tmp`, wiped on every cold start |
| One walk-forward | 3.17M bars, ~25 s | 60 s ceiling |
| Monte Carlo, 10,000 iterations | ~6 min across 14 worker processes | no multiprocessing, no long-lived process |

Anything deployed there that claimed to run a backtest would be running a toy
on truncated data, which is the one thing this project refuses to do. So the
pages are hosted and the work is local. Your data never leaves your machine —
the site sends instructions, not bars.

## If the site says the engine is offline

- Is the **Backtest Lab** window still open? Closing it stops the engine.
- Did it start on another port? The port is printed in that window. Put the
  address into **More → Connection** and press Reconnect.
- The browser must allow a page to reach `127.0.0.1`. Chrome, Edge and Firefox
  do by default; the server sends the private-network headers that Chrome
  requires for it.

## Running with no internet at all

The engine serves the same UI itself. Open <http://127.0.0.1:8765> and you get
an identical app with no Vercel involved. The hosted copy is a convenience, not
a dependency.
