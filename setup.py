"""A compression ASGI middleware using zstd.

Built using starlette under the hood, it can be used as a drop in replacement
to GZipMiddleware for Starlette or FastAPI, if you have a client that supports
it.
"""

from setuptools import setup  # type: ignore


setup(
    name="zstd-asgi",
    version="0.2",
    url="https://github.com/tuffnatty/zstd-asgi",
    license="MIT",
    author="Phil Krylov",
    author_email="phil@krylov.eu",
    description="Zstd compression ASGI middleware",
    long_description=__doc__,
    packages=["zstd_asgi"],
    python_requires=">=3.8",
    include_package_data=True,
    install_requires=["starlette>=0.13.4", "zstandard>=0.22.0"],
    platforms="any",
    zip_safe=False,
    classifiers=[
        "Environment :: Web Environment",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
