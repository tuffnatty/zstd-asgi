"""
HTTP Accept-Encoding header parsing and negotiation utilities.
"""
from functools import lru_cache
from typing import Literal, Union, Tuple

# Defines the internal priority for encodings we recognize.
# Lower numbers are preferred when q-factors are equal.
# "zstd" > "gzip" > "identity" > "*"
CODING_PRIORITIES: dict[str, int] = {
    "zstd": 0,
    "gzip": 1,
    "identity": 998,  # 'identity' means no compression
    "*": 999,         # '*' means any other encoding
}

# Defines the clear return types for the encoding selection
SupportedEncoding = Literal["zstd", "gzip", "identity"]


def parse_part(part: str) -> Union[Tuple[float, int, str], None]:
    """
    Parses a single part of the 'Accept-Encoding' header (e.g., "gzip;q=0.8").
    Returns a tuple of (q-factor, priority, encoding_name) or None if invalid.
    """
    part = part.strip()
    if not part:
        return None

    components = part.split(";")
    coding_name = components[0].strip()

    if coding_name not in CODING_PRIORITIES:
        # Ignore encodings we don't recognize or support (e.g., "br", "deflate")
        return None

    priority = CODING_PRIORITIES[coding_name]
    q_val = 1.0  # Default q-factor is 1.0 per RFC

    # Find the "q=" parameter, if it exists
    for param in components[1:]:
        param = param.strip()
        if param.startswith("q="):
            try:
                q_val = float(param[2:])
            except ValueError:
                q_val = 0.0  # A malformed q-factor (e.g., "q=foo") means "not acceptable"
            break
    
    if q_val <= 0:
        return None  # q=0 means the client explicitly forbids this encoding
    
    if q_val > 1.0:
        q_val = 1.0  # q-factor cannot exceed 1.0

    return (q_val, priority, coding_name)


@lru_cache(maxsize=128)
def get_preferred_encoding(
    accept_encoding: str, 
    gzip_fallback: bool
) -> SupportedEncoding:
    """
    Parses the 'Accept-Encoding' header string and returns the
    highest-priority encoding that the server supports ("zstd", "gzip", or "identity").
    
    Results are LRU-cached for performance.
    """
    options: list[tuple[float, int, str]] = []
    
    # 1. Parse the header into a list of valid, supported options
    for part_str in accept_encoding.split(","):
        parsed = parse_part(part_str)
        if parsed:
            options.append(parsed)
        
    # 2. Sort the options:
    options.sort(key=lambda p: (-p[0], p[1]))

    # 3. Find the first supported encoding in the sorted list
    for q, priority, name in options:
        if name == "zstd":
            return "zstd"
        
        if name == "gzip":
            if gzip_fallback:
                return "gzip"
            # If gzip_fallback=False, skip gzip and continue
        
        if name == "identity":
            return "identity"  # No compression
        
        if name == "*":
            # The wildcard "*" matches any encoding not already listed.
            # At this point, we apply server preference (zstd > gzip)
            # unless those encodings were explicitly forbidden.
            
            if "zstd;q=0" not in accept_encoding.replace(" ", ""):
                return "zstd"
            
            if gzip_fallback and "gzip;q=0" not in accept_encoding.replace(" ", ""):
                return "gzip"
            
            # If both are banned or gzip is not a fallback, use identity
            return "identity"

    # If no options were parsed (e.g., only "br;q=1.0" was sent)
    # or no supported encodings were found, default to no compression.
    # This is the correct fallback behavior.
    return "identity"