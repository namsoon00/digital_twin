"""Scoped ABox row persistence and activation, behind injected storage ports.

Candidate planning, projection leases and native rule execution stay outside
this package. Importing it never constructs a repository or opens a driver.
"""
