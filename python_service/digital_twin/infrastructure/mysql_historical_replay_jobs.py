"""Compatibility module for digital_twin.modules.outcomes.infrastructure.mysql_historical_replay_jobs."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.outcomes.infrastructure.mysql_historical_replay_jobs')
