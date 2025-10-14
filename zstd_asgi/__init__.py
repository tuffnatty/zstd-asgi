import io
import re
from typing import List, Union, NoReturn

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send

try:
    from compression.zstd import ZstdCompressor, CompressionParameter
except ImportError:
    from backports.zstd import ZstdCompressor, CompressionParameter


__version__ = "1.0"


class ZstdMiddleware:
    """Zstd middleware public interface."""

    def __init__(
        self,
        app: ASGIApp,
        level: int = 3,
        minimum_size: int = 500,
        threads: int = 0,
        write_checksum: bool = False,
        write_content_size: bool = True,
        gzip_fallback: bool = True,
        excluded_handlers: Union[List, None] = None,
    ) -> None:
        """
        Arguments.

        level: Integer compression level.
            Valid values are all negative integers through 22.
            Negative levels effectively engage --fast mode from the zstd CLI.
        minimum_size: Only compress responses that are bigger than this value in bytes.
        threads: Number of threads to use to compress data concurrently.
            When set, compression operations are performed on multiple threads.
            The default value (0) disables multi-threaded compression.
            A value of -1 means to set the number of threads to the number
            of detected logical CPUs.
        write_checksum: If True, a 4 byte content checksum will be written with
            the compressed data, allowing the decompressor to perform content
            verification.
        write_content_size: If True (the default), the decompressed content size
            will be included in the header of the compressed data. This data
            will only be written if the compressor knows the size of the input
            data.
        gzip_fallback: If True, uses gzip encoding if 'zstd' is not in the Accept-Encoding header.
        excluded_handlers: List of handlers to be excluded from being compressed.
        """
        self.app = app
        self.level = level
        self.minimum_size = minimum_size
        self.threads = threads
        self.write_checksum = write_checksum
        self.write_content_size = write_content_size
        self.gzip_fallback = gzip_fallback
        if excluded_handlers:
            self.excluded_handlers = [re.compile(path) for path in excluded_handlers]
        else:
            self.excluded_handlers = []

    async def __call__(self,
                       scope: Scope,
                       receive: Receive,
                       send: Send) -> None:
        if self._is_handler_excluded(scope) or scope["type"] != "http":
            return await self.app(scope, receive, send)
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

    def _is_handler_excluded(self, scope: Scope) -> bool:
        handler = scope.get("path", "")

        return any(pattern.search(handler) for pattern in self.excluded_handlers)


class ZstdResponder:
    def __init__(
        self,
        app: ASGIApp,
        level: int,
        threads: int,
        write_checksum: bool,
        write_content_size: bool,
        minimum_size: int,
    ) -> None:
        self.app = app
        self.level = level
        self.minimum_size = minimum_size
        self.send = unattached_send  # type: Send
        self.initial_message = {}  # type: Message
        self.started = False
        self.content_encoding_set = False
        self.zstd_compressor = ZstdCompressor(
            options={CompressionParameter.compression_level: level,
                     CompressionParameter.nb_workers: threads,
                     CompressionParameter.checksum_flag: write_checksum,
                     CompressionParameter.content_size_flag: write_content_size},
        )

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
            headers = Headers(raw=self.initial_message["headers"])
            self.content_encoding_set = "content-encoding" in headers
        elif message_type == "http.response.body" and self.content_encoding_set:
            if not self.started:
                self.started = True
                await self.send(self.initial_message)
            await self.send(message)
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
                body = self.zstd_compressor.compress(body,
                        ZstdCompressor.FLUSH_FRAME)

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

                message["body"] = self.zstd_compressor.compress(body,
                        ZstdCompressor.FLUSH_BLOCK)

                await self.send(self.initial_message)
                await self.send(message)

        elif message_type == "http.response.body":
            # Remaining body in streaming Zstd response.
            body = message.get("body", b"")
            more_body = message.get("more_body", False)

            message["body"] = self.zstd_compressor.compress(body,
                    ZstdCompressor.FLUSH_BLOCK if more_body
                    else ZstdCompressor.FLUSH_FRAME)

            await self.send(message)


async def unattached_send(message: Message) -> NoReturn:
    raise RuntimeError("send awaitable not set")  # pragma: no cover
