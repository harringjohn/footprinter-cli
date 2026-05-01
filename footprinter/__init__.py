"""Footprinter — digital life indexer with AI-powered context."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("footprinter-cli")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
