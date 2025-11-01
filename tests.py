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
        return zstd.decompress(response.content)
else:
    if 'zstd' in httpx._decoders.SUPPORTED_DECODERS:
        def decompressed_response(response):
            return response.content
    else:
        # no transparent zstd support in httpx yet
        def decompressed_response(response):
            return zstd.decompress(response.content)
        

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


@pytest.mark.parametrize(
    "accept_encoding, gzip_fallback, expected_encoding",
    [
        # 1. zstd has higher q-factor than gzip
        ("zstd;q=1.0, gzip;q=0.5", True, "zstd"),
        
        # 2. gzip has higher q-factor than zstd (gzip_fallback=True)
        ("zstd;q=0.5, gzip;q=1.0", True, "gzip"),
        
        # 3. zstd is preferred when q-factors are equal (tests CODING_PRIORITIES)
        ("zstd;q=0.8, gzip;q=0.8", True, "zstd"),
        ("gzip;q=0.8, zstd;q=0.8", True, "zstd"),
        
        # 4. Unsupported 'br' has highest q, fallback to zstd
        ("br;q=1.0, zstd;q=0.9, gzip;q=0.8", True, "zstd"),
        
        # 5. zstd is forbidden (q=0) -> select gzip
        ("zstd;q=0, gzip;q=1.0", True, "gzip"),
        
        # 6. Both zstd and gzip are forbidden -> select identity (no compression)
        ("zstd;q=0, gzip;q=0", True, "identity"),
        
        # 7. Wildcard (*) matches zstd/gzip (zstd preferred)
        ("br;q=1.0, *;q=0.5", True, "zstd"),
        
        # 8. Wildcard, zstd forbidden -> select gzip
        ("zstd;q=0, *;q=0.5", True, "gzip"),
        
        # 9. identity (no compression) is preferred
        ("zstd;q=0.1, identity;q=0.5", True, "identity"),
        
        # 10. identity is explicitly forbidden -> select zstd
        ("identity;q=0, zstd;q=0.5", True, "zstd"),
        
        # --- Scenarios with gzip_fallback=False ---
        
        # 11. gzip preferred but fallback=False -> select zstd
        ("zstd;q=0.5, gzip;q=1.0", False, "zstd"),
        
        # 12. zstd forbidden, gzip preferred, but fallback=False -> select identity
        ("zstd;q=0, gzip;q=1.0", False, "identity"),
        
        # 13. Only gzip available, but fallback=False -> select identity
        ("gzip;q=1.0", False, "identity"),
    ],
)
def test_respect_q_factors(
    test_client_factory, accept_encoding, gzip_fallback, expected_encoding
):
    """
    Tests that q-factor negotiation works correctly when respect_q_factors=True.
    """
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(
        ZstdMiddleware,
        respect_q_factors=True,  # <-- The core flag for this test
        gzip_fallback=gzip_fallback
    )

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": accept_encoding})
    
    assert response.status_code == 200
    
    if expected_encoding == "zstd":
        assert response.headers["Content-Encoding"] == "zstd"
        assert decompressed_response(response) == b"x" * 4000
        assert int(response.headers["Content-Length"]) < 4000
    elif expected_encoding == "gzip":
        # TestClient (httpx) decompresses gzip automatically.
        assert response.headers["Content-Encoding"] == "gzip"
        assert response.text == "x" * 4000 
        assert int(response.headers["Content-Length"]) < 4000
    elif expected_encoding == "identity":
        assert "Content-Encoding" not in response.headers
        assert response.text == "x" * 4000
        assert int(response.headers["Content-Length"]) == 4000


def test_q_factors_ignored_by_default(test_client_factory):
    """
    Tests that when respect_q_factors=False (default),
    q-factors are ignored and zstd is chosen if present.
    """
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    app = Starlette(routes=[Route("/", homepage)])
    # Do not set respect_q_factors flag (defaults to False)
    app.add_middleware(ZstdMiddleware, gzip_fallback=True) 

    client = test_client_factory(app)
    
    # gzip is preferred (q=1.0), but 'zstd' is present in the header
    headers = {"accept-encoding": "zstd;q=0.5, gzip;q=1.0"}
    response = client.get("/", headers=headers)
    
    # Default logic ('zstd' in header) should run and select zstd
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "zstd"
    assert decompressed_response(response) == b"x" * 4000