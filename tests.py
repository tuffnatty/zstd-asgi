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
from starlette.testclient import TestClient

import zstandard

from zstd_asgi import ZstdMiddleware


@pytest.fixture
def test_client_factory(anyio_backend_name, anyio_backend_options):
    return functools.partial(
        TestClient,
        backend=anyio_backend_name,
        backend_options=anyio_backend_options,
    )


def test_zstd_responses(test_client_factory):
    app = Starlette()

    app.add_middleware(ZstdMiddleware)

    @app.route("/")
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "zstd"

    # no transparent zstd support in requests yet
    assert zstandard.decompress(response.content, 5000) == b"x" * 4000
    assert int(response.headers["Content-Length"]) < 4000


def test_zstd_not_in_accept_encoding(test_client_factory):
    app = Starlette()

    app.add_middleware(ZstdMiddleware)

    @app.route("/")
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "identity"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_zstd_ignored_for_small_responses(test_client_factory):
    app = Starlette()

    app.add_middleware(ZstdMiddleware)

    @app.route("/")
    def homepage(request):
        return PlainTextResponse("OK", status_code=200)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.text == "OK"
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 2


def test_zstd_streaming_response(test_client_factory):
    app = Starlette()

    app.add_middleware(ZstdMiddleware)

    @app.route("/")
    def homepage(request):
        async def generator(bytes, count):
            for index in range(count):
                yield bytes

        streaming = generator(bytes=b"x" * 400, count=10)
        return StreamingResponse(streaming, status_code=200)

    client = test_client_factory(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "zstd"
    assert zstandard.decompress(response.content, 5000) == b"x" * 4000
    assert "Content-Length" not in response.headers


def test_zstd_api_options():
    """Tests default values overriding."""
    app = Starlette()

    app.add_middleware(
        ZstdMiddleware, level=19, write_checksum=True, threads=2,
    )

    @app.route("/")
    def homepage(request):
        return JSONResponse({"data": "a" * 4000}, status_code=200)

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200


def test_gzip_fallback():
    app = Starlette()

    app.add_middleware(ZstdMiddleware, gzip_fallback=True)

    @app.route("/")
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert response.headers["Content-Encoding"] == "gzip"
    assert int(response.headers["Content-Length"]) < 4000


def test_gzip_fallback_false():
    app = Starlette()

    app.add_middleware(ZstdMiddleware, gzip_fallback=False)

    @app.route("/")
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "gzip"})
    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_excluded_handlers():
    app = Starlette()

    app.add_middleware(
        ZstdMiddleware,
        excluded_handlers=["/excluded"],
    )

    @app.route("/excluded")
    def homepage(request):
        return PlainTextResponse("x" * 4000, status_code=200)

    client = TestClient(app)
    response = client.get("/excluded", headers={"accept-encoding": "zstd"})

    assert response.status_code == 200
    assert response.text == "x" * 4000
    assert "Content-Encoding" not in response.headers
    assert int(response.headers["Content-Length"]) == 4000


def test_zstd_avoids_double_encoding():
    # See https://github.com/encode/starlette/pull/1901

    app = Starlette()

    app.add_middleware(ZstdMiddleware, minimum_size=1)

    @app.route("/")
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
                "x-gzipped-content-length": str(len(body))
            }
        )

    client = TestClient(app)
    response = client.get("/", headers={"accept-encoding": "zstd"})
    assert response.status_code == 200
    assert response.text == "hello world" * 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Content-Length"] == response.headers["x-gzipped-content-length"]
