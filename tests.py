"""Main tests for zstd middleware.

Some of these tests are the same as the ones from starlette.tests.middleware.test_gzip
but using zstd instead.
"""

import functools
import gzip
import io

import pytest

from starlette.applications import Starlette
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route
from starlette.testclient import TestClient

try:
    from compression import zstd
except ImportError:
    from backports import zstd

try:
    from starlette.testclient import httpx
except ImportError:
    # starlette does not use httpx yet
    def decompressed_response(response):
        return zstd.decompress(response)
else:
    if 'zstd' in httpx._decoders.SUPPORTED_DECODERS:
        def decompressed_response(response):
            return response.content
    else:
        # no transparent zstd support in httpx yet
        def decompressed_response(response):
            return zstd.decompress(response)
        

from zstd_asgi import ZstdMiddleware


@pytest.fixture
def test_client_factory(anyio_backend_name, anyio_backend_options):
    return functools.partial(
        TestClient,
        backend=anyio_backend_name,
        backend_options=anyio_backend_options,
    )


def test_zstd_responses(test_client_factory):
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ZstdMiddleware)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "zstd"
    assert decompressed_response(response) == b"x" * 4000
    assert int(response.headers["Content-Length"]) < 4000


def test_zstd_not_in_accept_encoding(test_client_factory):
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])

    app.add_middleware(ZstdMiddleware)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "identity"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_zstd_ignored_for_small_responses(test_client_factory):
    def homepage(request):
        return PlainTextResponse("OK", status_code=200)

    app = Starlette(routes=[Route("/", homepage)])

    app.add_middleware(ZstdMiddleware)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.text == "OK"
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 2


def test_zstd_streaming_response(test_client_factory):
    def homepage(request):
        async def generator(bytes, count):
            for index in range(count):
                yield bytes

        streaming = generator(bytes=b"x" * 400, count=10)
        return StreamingResponse(streaming, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ZstdMiddleware)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "zstd"
    assert decompressed_response(response) == b"x" * 4000
    assert "Content-Length" not in response.headers


def test_zstd_api_options(test_client_factory):
    def homepage(request):
        return JSONResponse({"data": "a" * 4000}, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(
        ZstdMiddleware,
        level=19,
        write_checksum=True,
        threads=2,
    )

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200


def test_gzip_fallback(test_client_factory):
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ZstdMiddleware, gzip_fallback=True)

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert response.headers["Content-Encoding"] == "gzip"
    assert int(response.headers["Content-Length"]) < 4000


def test_gzip_fallback_false(test_client_factory):
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ZstdMiddleware, gzip_fallback=False)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_excluded_handlers(test_client_factory):
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/excluded", homepage)])
    app.add_middleware(
        ZstdMiddleware,
        excluded_handlers=["/excluded"],
    )

    client = test_client_factory(app)
    response = client.get("/excluded", headers={"accept-encoding": "zstd"})

    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_zstd_avoids_double_encoding(test_client_factory):
    # See https://github.com/encode/starlette/pull/1901
    def homepage(request):
        gzip_buffer = io.BytesIO()
        gzip_file = gzip.GzipFile(mode="wb", fileobj=gzip_buffer)
        gzip_file.write(b"hello world" * 200)
        gzip_file.close()
        body = gzip_buffer.getvalue()
        return Response(
            body,
            headers={
                "content-encoding": "gzip",
                "x-gzipped-content-length": str(len(body)),
            },
        )

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(ZstdMiddleware, minimum_size=1)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.text == "hello world" * 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert (
        response.headers["Content-Length"]
        == response.headers["x-gzipped-content-length"]
    )
