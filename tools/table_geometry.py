"""Dependency-free geometry contract shared by table producers and consumers."""

# Existing ruled-table containment tolerance, in PDF points. Preserve source
# geometry while applying the same boundary in extraction and validation.
TABLE_EPSILON = 0.25
