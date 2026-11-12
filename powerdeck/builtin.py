"""Built-in profiles (v1: Default, RemoteWork, BatterySaver, Gaming)."""
from .profile import Profile

BUILTIN_PROFILES = [
    Profile(
        name="Default",
        description="Safe everyday mode: lid suspends, automatic suspend on.",
        lid={"battery": "suspend", "ac": "suspend", "docked": "suspend"},
        automatic_suspend={"battery": True, "ac": True},
        wifi_powersave="default",
        power_mode="balanced",
        builtin=True,
    ),
    Profile(
        name="RemoteWork",
        description="Laptop stays awake and connected with the lid closed.",
        lid={"battery": "ignore", "ac": "ignore", "docked": "ignore"},
        automatic_suspend={"battery": False, "ac": False},
        wifi_powersave="off",
        power_mode="balanced",
        thermal_guard={"warn_celsius": 85, "critical_celsius": 92},
        builtin=True,
    ),
    Profile(
        name="BatterySaver",
        description="Longest battery life.",
        lid={"battery": "suspend", "ac": "suspend", "docked": "suspend"},
        automatic_suspend={"battery": True, "ac": True},
        wifi_powersave="on",
        power_mode="power-saver",
        builtin=True,
    ),
    Profile(
        name="Gaming",
        description="Maximum performance.",
        automatic_suspend={"battery": True, "ac": False},
        wifi_powersave="default",
        power_mode="performance",
        builtin=True,
    ),
]
