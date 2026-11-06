# This file makes the src directory a Python package
import warnings

# Suppress upstream library deprecation warnings (e.g. Python 3.14 / google-genai)
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module=r"google\.genai.*"
)
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, message=r".*_UnionGenericAlias.*"
)
