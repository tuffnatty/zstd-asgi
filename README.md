# zstd-asgi

[![Packaging status](https://img.shields.io/pypi/v/zstd-asgi?color=%2334D058&label=pypi%20package)](https://pypi.org/project/zstd-asgi)
[![CI](https://github.com/tuffnatty/zstd-asgi/workflows/Tests/badge.svg)](https://github.com/tuffnatty/zstd-asgi/actions?query=workflow%3ATests)



`ZstdMiddleware` adds [Zstd](https://github.com/facebook/zstd) response compression to ASGI applications (Starlette, FastAPI, Quart, etc.). It provides faster and more dense compression than GZip, and can be used as a drop in replacement for the `GZipMiddleware` shipped with Starlette.

**Installation**

```bash
pip install zstd-asgi
```

## Examples

### Starlette

```python
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware

from zstd_asgi import ZstdMiddleware

async def homepage(request):
    return JSONResponse({"data": "a" * 4000})

app = Starlette(
  routes=[Route("/", homepage)],
  middleware=[Middleware(ZstdMiddleware)],
)
```

### FastAPI

```python
from fastapi import FastAPI
from zstd_asgi import ZstdMiddleware

app = FastAPI()
app.add_middleware(ZstdMiddleware)

@app.get("/")
def home() -> dict:
    return {"data": "a" * 4000}
```

## API Reference

**Overview**

```python
app.add_middleware(
  ZstdMiddleware,
  level=3,
  minimum_size=500,
  threads=0,
  write_checksum=True,
  write_content_size=False,
  gzip_fallback=True
)
```

**Parameters**:

- `level`: Compression level. Valid values are -2¹⁷ to 22.
- `minimum_size`: Only compress responses that are bigger than this value in bytes.
- `threads`: Number of threads to use to compress data concurrently. When set, compression operations are performed on multiple threads. The default value (0) disables multi-threaded compression. A value of -1 means to set the number of threads to the number of detected logical CPUs.
- `write_checksum`: If True, a 4 byte content checksum will be written with the compressed data, allowing the decompressor to perform content verification.
- `write_content_size`: If True (the default), the decompressed content size will be included in the header of the compressed data. This data will only be written if the compressor knows the size of the input data.
- `gzip_fallback`: If `True`, uses gzip encoding if `zstd` is not in the Accept-Encoding header.

## Performance

A simple comparative example using Python `sys.getsizof()` and `timeit`:

```python
# ipython console
import gzip
import sys

import brotli
import requests
import zstandard

page = requests.get("https://github.com/fullonic/brotli-asgi").content
%timeit zstandard.ZstdCompressor(level=3).compress(page)
# 788 µs ± 9.99 µs per loop (mean ± std. dev. of 7 runs, 1000 loops each)
sys.getsizeof(zstandard.ZstdCompressor(level=3).compress(page))
# 36381
%timeit brotli.compress(page, quality=4)
# 2.55 ms ± 142 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)
sys.getsizeof(brotli.compress(page, quality=4))
# 34361
%timeit gzip.compress(page, compresslevel=6)
# 4.05 ms ± 95 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)
sys.getsizeof(gzip.compress(page, compresslevel=6))
# 36760
```

## Compatibility

- [RFC 8878](https://tools.ietf.org/search/rfc8878)
- [Zstd nginx module](https://github.com/tokers/zstd-nginx-module)
- [wget2](https://gitlab.com/gnuwget/wget2)
- Browser support is not known.
