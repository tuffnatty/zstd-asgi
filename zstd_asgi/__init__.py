import io

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import zstandard


class ZstdMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        level: int = 3,
        minimum_size: int = 500,
        threads: int = 0,
        write_checksum: bool = False,
        write_content_size: bool = True,
        gzip_fallback: bool = True,
    ) -> None:
        self.app = app
        self.level = level
        self.minimum_size = minimum_size
        self.threads = threads
        self.write_checksum = write_checksum
        self.write_content_size = write_content_size
        self.gzip_fallback = gzip_fallback

    async def __call__(self,
                       scope: Scope,
                       receive: Receive,
                       send: Send) -> None:
        if scope["type"] == "http":
            accept_encoding = Headers(scope=scope).get("Accept-Encoding", "")
            if "zstd" in accept_encoding:
                responder = ZstdResponder(
                    self.app,
                    self.level,
                    self.threads,
                    self.write_checksum,
                    self.write_content_size,
                    self.minimum_size,
                )
                await responder(scope, receive, send)
                return
            if self.gzip_fallback and "gzip" in accept_encoding:
                responder = GZipResponder(self.app, self.minimum_size)
                await responder(scope, receive, send)
                return
        await self.app(scope, receive, send)


class ZstdResponder:
    def __init__(
        self,
        app: ASGIApp,
        level: int,
        minimum_size: int,
        threads: int,
        write_checksum: bool,
        write_content_size: bool,
    ) -> None:
        self.app = app
        self.level = level
        self.minimum_size = minimum_size
        self.send = unattached_send  # type: Send
        self.initial_message = {}  # type: Message
        self.started = False
        self.zstd_buffer = io.BytesIO()
        self.zstd_file = zstandard.ZstdCompressor(
            level=level,
            threads=threads,
            write_checksum=write_checksum,
            write_content_size=write_content_size,
        ).stream_writer(self.zstd_buffer)

    async def __call__(self,
                       scope: Scope,
                       receive: Receive,
                       send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self.send_with_zstd)

    async def send_with_zstd(self, message: Message) -> None:
        message_type = message["type"]
        if message_type == "http.response.start":
            # Don't send the initial message until we've determined how to
            # modify the outgoing headers correctly.
            self.initial_message = message
        elif message_type == "http.response.body" and not self.started:
            self.started = True
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            if len(body) < self.minimum_size and not more_body:
                # Don't apply Zstd to small outgoing responses.
                await self.send(self.initial_message)
                await self.send(message)
            elif not more_body:
                # Standard Zstd response.
                self.zstd_file.write(body)
                self.zstd_file.flush(zstandard.FLUSH_FRAME)
                body = self.zstd_buffer.getvalue()
                self.zstd_file.close()

                headers = MutableHeaders(raw=self.initial_message["headers"])
                headers["Content-Encoding"] = "zstd"
                headers["Content-Length"] = str(len(body))
                headers.add_vary_header("Accept-Encoding")
                message["body"] = body

                await self.send(self.initial_message)
                await self.send(message)
            else:
                # Initial body in streaming Zstd response.
                headers = MutableHeaders(raw=self.initial_message["headers"])
                headers["Content-Encoding"] = "zstd"
                headers.add_vary_header("Accept-Encoding")
                del headers["Content-Length"]

                self.zstd_file.write(body)
                self.zstd_file.flush()
                message["body"] = self.zstd_buffer.getvalue()
                self.zstd_buffer.seek(0)
                self.zstd_buffer.truncate()

                await self.send(self.initial_message)
                await self.send(message)

        elif message_type == "http.response.body":
            # Remaining body in streaming Zstd response.
            body = message.get("body", b"")
            more_body = message.get("more_body", False)

            self.zstd_file.write(body)
            if not more_body:
                self.zstd_file.flush(zstandard.FLUSH_FRAME)
                message["body"] = self.zstd_buffer.getvalue()
                self.zstd_file.close()
            else:
                message["body"] = self.zstd_buffer.getvalue()
                self.zstd_buffer.seek(0)
                self.zstd_buffer.truncate()

            await self.send(message)


async def unattached_send(message: Message) -> None:
    raise RuntimeError("send awaitable not set")  # pragma: no cover
