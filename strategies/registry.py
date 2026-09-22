# Strategy Registry
# ===============
# Register every strategy here. The engine auto-discovers these
# and displays them in the menu.

from strategies.base import Strategy
from strategies.hilega_milega import HilegaMilegaStrategy

# Auto-register all subclasses of Strategy discovered in the strategies/ folder
# This makes adding new strategies as simple as creating a new file.

STRATEGY_REGISTRY = {}

def register_strategy(strategy_class):
    """Decorator to register a strategy class."""
    instance = strategy_class()
    STRATEGY_REGISTRY[instance.key] = {
        "name": instance.name,
        "compute_func": instance.compute,
        "buy_condition": instance.buy_signal,
        "sell_condition": instance.sell_signal,
        "metrics_formatter": instance.format_metrics,
        "sort_metric": instance.sort_value,
        "strategy_obj": instance,  # keep reference for utilities
    }
    return strategy_class

# Register built-in strategies
@register_strategy
class _BuiltInHilegaMilega(HilegaMilegaStrategy):
    pass

# At module load time, populate registry
# (The @register_strategy decorator already added above)

# Expose for easy access
def get_registry():
    """Return the full strategy registry dict."""
    return STRATEGY_REGISTRY

def list_strategies():
    """Return list of strategy display names."""
    return {k: v["name"] for k, v in STRATEGY_REGISTRY.items()}

def get_strategy(key: str):
    """Return strategy config dict by key, or None."""
    return STRATEGY_REGISTRY.get(key)