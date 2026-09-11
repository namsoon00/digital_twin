"""Compatibility module for digital_twin.modules.news_intelligence.infrastructure.mysql_investment_research."""

from importlib import import_module
import sys

sys.modules[__name__] = import_module('digital_twin.modules.news_intelligence.infrastructure.mysql_investment_research')
