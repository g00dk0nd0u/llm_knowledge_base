"""Dependency-free constants shared by PDF ingestion and metadata consumers."""

LOW_TEXT_THRESHOLD = 40
MANY_DRAWINGS_THRESHOLD = 200

# Existing Pipeline v2 containment tolerance, in PDF points. Consumers must
# preserve the same source geometry rather than reject accepted span links.
TABLE_EPSILON = 0.25
