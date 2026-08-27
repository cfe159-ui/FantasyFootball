"""Fantasy football analysis agent: Yahoo league state + free public NFL data."""
import warnings

# Registered before any submodule imports urllib3 (via requests). macOS system
# Python links LibreSSL, which urllib3 v2 warns about on every run.
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
try:
    from urllib3.exceptions import NotOpenSSLWarning
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError:
    pass

__version__ = "0.1.0"
