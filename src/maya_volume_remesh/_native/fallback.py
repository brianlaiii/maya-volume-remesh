"""Automatic fallback diagnostics. This standalone project has no modal UI."""

import traceback
import warnings


class FallbackCancelled(KeyboardInterrupt):
    """Compatibility exception for callers that cancel their own operation."""


def failure_details(error):
    if isinstance(error, BaseException):
        return "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()
    return str(error)


def require_python_fallback(tool, reason=None):
    """Legacy helper name. Auto mode is authorized to continue without a dialog."""
    warnings.warn(
        "{}: using Python. {}".format(tool, failure_details(reason or "Native runtime unavailable")),
        RuntimeWarning,
        stacklevel=2,
    )
