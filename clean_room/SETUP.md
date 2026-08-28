# Setup — read before reporting "no Python on this machine"

## Use this interpreter

```
C:\Users\DELL\pyembed312\python.exe
```

An embeddable Python 3.12.10 with the project's dependencies installed. It is
outside the repo, so it survives `git clean`.

```bash
C:/Users/DELL/pyembed312/python.exe clean_room/inspect_dataset.py --dataset <file>
```

## What NOT to use, and why

| Path | Problem |
|---|---|
| `python` / `python3` on PATH | Microsoft Store stubs, not interpreters |
| `.venv/` in this repo | **Broken.** `pyvenv.cfg` points at `C:\Users\Ayush\...Python312` and was created against `E:\Claude DND` — it came from a different machine and user. Its shim cannot find its base install |

The `.venv` is left in place rather than deleted because it is checked into the
project's history; it simply cannot run here.

`sqlite3` is also not on PATH. Query the datasets through Python's built-in
`sqlite3` module instead.

## Rebuilding from scratch

If the interpreter above is missing, the embeddable distribution avoids needing
admin rights (the official installer fails on this machine with MSI error
0x80070003):

```powershell
Invoke-WebRequest https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip -OutFile py.zip
Expand-Archive py.zip -DestinationPath C:\Users\DELL\pyembed312
# uncomment "import site" in python312._pth, then:
Invoke-WebRequest https://bootstrap.pypa.io/get-pip.py -OutFile get-pip.py
C:\Users\DELL\pyembed312\python.exe get-pip.py
C:\Users\DELL\pyembed312\python.exe -m pip install fastapi jinja2 logzero python-multipart pyotp `
    smartapi-python tzdata "uvicorn[standard]" websocket-client pytest ruff httpx2
```

Add the repo's `src/` to `python312._pth` so `options_bot` imports without
`PYTHONPATH` (which the embeddable build ignores).

## Long-running jobs

Backtests over the full archive take minutes. Two traps, both hit during
development:

- **Never pipe to `tail`** while waiting — it buffers until the process exits, so
  a running job looks dead.
- **Use `python -u`** and redirect straight to a file, then read the file.

Queries that wrap a column in a function (`date(started_at) >= ?`) or use `LIKE`
on the token disable the indexes and turn a lookup into a scan of millions of
rows. ISO timestamps compare lexicographically, so `started_at >= ?` works and
stays indexed.
