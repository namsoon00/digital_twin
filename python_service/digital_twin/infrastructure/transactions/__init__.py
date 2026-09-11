"""Explicit connection-bound coordinators for atomic multi-owner changes.

These adapters are composed by platform infrastructure, never by business modules.
They retain existing database transactions, not asynchronous replacement writes.
"""
