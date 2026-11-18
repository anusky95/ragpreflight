"""ragpreflight — lint your knowledge before your agent reads it.

    >>> from ragpreflight import scan
    >>> report = scan("./docs")
    >>> report.verdict, report.score
    >>> for f in report.findings: print(f.severity.value, f.title, f.path)
"""

__version__ = "0.3.0"

from .models import Document, Finding, Report, Severity  # noqa: E402
from .scan import scan  # noqa: E402

__all__ = ["scan", "Report", "Finding", "Severity", "Document", "__version__"]
